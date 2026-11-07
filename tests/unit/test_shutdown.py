"""Shutdown contract: release the link in Stage 1, latch, and never connect again."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryNotReady  # stub from conftest


async def _hang_forever(*args, **kwargs):
    await asyncio.Event().wait()


class _Client:
    def __init__(self, hang_disconnect: bool = False) -> None:
        self.hang_disconnect = hang_disconnect
        self.disconnects = 0

    async def read_gatt_char(self, char):
        await asyncio.Event().wait()  # a GATT step stuck mid-sync

    async def write_gatt_char(self, char, data, response=True):
        await asyncio.Event().wait()

    async def disconnect(self):
        self.disconnects += 1
        if self.hang_disconnect:
            await asyncio.Event().wait()


async def _setup(package, hass, entry):
    assert await package.async_setup_entry(hass, entry) is True
    return hass.data["lywsd02"]["e1"]


def _entry_job(hass, name_part="release BLE link"):
    return next(j for j in hass.shutdown_jobs if name_part in j.name)


def test_per_entry_job_registered_and_removed_on_unload(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    monkeypatch.setattr(coordinator_mod, "establish_connection", _hang_forever)

    async def run():
        hass, entry = make_hass(), make_entry()
        await _setup(package, hass, entry)
        assert [j.name for j in hass.shutdown_jobs] == ["lywsd02 release BLE link Clock"]
        for callback in entry.unload_callbacks:  # what HA does on unload
            callback()
        assert hass.shutdown_jobs == []
        for task in entry.tasks:
            task.cancel()

    asyncio.run(run())


def test_job_releases_held_link_cancels_sync_and_latches(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    client = _Client()
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)

    async def run():
        hass, entry = make_hass(), make_entry()
        coordinator = await _setup(package, hass, entry)
        await asyncio.sleep(0.05)  # first sync is connected and stuck on a read
        assert connect.await_count == 1 and not entry.tasks[0].done()

        await _entry_job(hass).target()

        assert entry.tasks[0].done()
        assert client.disconnects == 1
        assert coordinator._client is None
        assert package.shutdown.in_progress(hass)
        # A button press / select change / scheduled tick afterwards never connects,
        # and the refusal is not recorded as a failed sync.
        await coordinator.async_sync(reason="button")
        await coordinator.async_sync(reason="display units", units="°C")
        await coordinator.async_refresh()
        assert connect.await_count == 1
        assert coordinator.state.last_error is None

    asyncio.run(run())


def test_job_cancels_sync_stuck_in_connect(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    monkeypatch.setattr(coordinator_mod, "establish_connection", _hang_forever)

    async def run():
        hass, entry = make_hass(), make_entry()
        coordinator = await _setup(package, hass, entry)
        await asyncio.sleep(0.05)
        start = time.monotonic()
        await _entry_job(hass).target()
        assert time.monotonic() - start < 1
        assert entry.tasks[0].done()
        assert coordinator.state.last_error is None  # cancelled, not failed

    asyncio.run(run())


def test_hanging_disconnect_is_bounded_and_never_raises(pkg, make_hass, make_entry, monkeypatch, caplog):
    package, coordinator_mod = pkg
    client = _Client(hang_disconnect=True)
    monkeypatch.setattr(coordinator_mod, "establish_connection", AsyncMock(return_value=client))
    monkeypatch.setattr(package, "SHUTDOWN_RELEASE_TIMEOUT", 0.2)

    async def run():
        hass, entry = make_hass(), make_entry()
        await _setup(package, hass, entry)
        await asyncio.sleep(0.05)
        start = time.monotonic()
        with caplog.at_level("INFO"):
            await _entry_job(hass).target()  # must not raise
        assert time.monotonic() - start < 1.5
        assert package.shutdown.in_progress(hass)
        assert any(
            r.levelname == "WARNING" and "failed after" in r.getMessage() for r in caplog.records
        )

    asyncio.run(run())


def test_successful_release_logs_info(pkg, make_hass, make_entry, monkeypatch, caplog):
    package, coordinator_mod = pkg
    monkeypatch.setattr(coordinator_mod, "establish_connection", AsyncMock(return_value=_Client()))

    async def run():
        hass, entry = make_hass(), make_entry()
        await _setup(package, hass, entry)
        await asyncio.sleep(0.05)
        with caplog.at_level("INFO"):
            await _entry_job(hass).target()
        assert any("Released BLE link to Clock at shutdown in" in r.getMessage() for r in caplog.records)

    asyncio.run(run())


def test_setup_refuses_while_latched(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    connect = AsyncMock()
    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)

    async def run():
        hass, entry = make_hass(), make_entry()
        package.shutdown.begin(hass)
        with pytest.raises(ConfigEntryNotReady):
            await package.async_setup_entry(hass, entry)
        assert hass.shutdown_jobs == [] and entry.tasks == []
        assert "e1" not in hass.data["lywsd02"]
        connect.assert_not_called()

    asyncio.run(run())


def test_setup_tears_down_if_latch_sets_during_setup(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    connect = AsyncMock()
    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)

    async def run():
        hass, entry = make_hass(), make_entry()

        async def forward(*args):
            package.shutdown.begin(hass)  # Stage 1 starts while platforms load

        hass.config_entries.async_forward_entry_setups = forward
        with pytest.raises(ConfigEntryNotReady):
            await package.async_setup_entry(hass, entry)
        hass.config_entries.async_unload_platforms.assert_awaited_once()
        assert "e1" not in hass.data["lywsd02"]
        assert entry.tasks == []  # no first sync was started
        connect.assert_not_called()

    asyncio.run(run())


def test_latch_set_while_connecting_disconnects_without_failure(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    client = _Client()

    async def connect(*args, **kwargs):
        package.shutdown.begin(coordinator.hass)  # latched just as the link comes up
        return client

    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)

    async def run():
        nonlocal coordinator
        hass, entry = make_hass(), make_entry()
        coordinator = coordinator_mod.Lywsd02Coordinator(hass, entry)
        await coordinator._async_sync(reason="test")
        assert client.disconnects == 1
        assert coordinator.state.last_error is None and coordinator.state.last_sync is None

    coordinator = None
    asyncio.run(run())


def test_domain_latch_job_sets_flag_and_outlives_unload(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    monkeypatch.setattr(coordinator_mod, "establish_connection", _hang_forever)

    async def run():
        hass, entry = make_hass(), make_entry()
        assert await package.async_setup(hass, {}) is True
        latch = _entry_job(hass, "shutdown latch")
        await _setup(package, hass, entry)
        for callback in entry.unload_callbacks:  # entry unloaded (e.g. by release_link)
            callback()
        assert [j.name for j in hass.shutdown_jobs] == ["lywsd02 shutdown latch"]
        assert not package.shutdown.in_progress(hass)
        await latch.target()
        assert package.shutdown.in_progress(hass)
        for task in entry.tasks:
            task.cancel()

    asyncio.run(run())
