"""Startup contract: setup returns fast however badly the clock behaves."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lywsd02.const import encode_time  # noqa: E402 - package loaded by conftest


class _HangingClient:
    """Connects fine, then every GATT call hangs forever."""

    def __init__(self) -> None:
        self.disconnected = False

    async def read_gatt_char(self, char):
        await asyncio.Event().wait()

    async def write_gatt_char(self, char, data, response=True):
        await asyncio.Event().wait()

    async def disconnect(self):
        self.disconnected = True


class _GoodClient:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    async def read_gatt_char(self, char):
        # The clock shows exactly "now" in UTC (the stubbed local zone).
        return encode_time(int(datetime.now(timezone.utc).timestamp()), 0)

    async def write_gatt_char(self, char, data, response=True):
        self.writes.append(data)

    async def disconnect(self):
        pass


async def _hang_forever(*args, **kwargs):
    await asyncio.Event().wait()


def test_setup_returns_at_once_when_connect_hangs_forever(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    monkeypatch.setattr(coordinator_mod, "establish_connection", _hang_forever)
    monkeypatch.setattr(coordinator_mod, "STEP_TIMEOUT", 0.3)

    async def run():
        hass, entry = make_hass(), make_entry()
        start = time.monotonic()
        assert await package.async_setup_entry(hass, entry) is True
        elapsed = time.monotonic() - start
        coordinator = hass.data["lywsd02"]["e1"]
        task = entry.tasks[0]
        assert not task.done(), "the first check must run in the background"
        assert elapsed < 0.2
        # The background check gives up after the step timeout, with a reason.
        await asyncio.wait_for(task, 2)
        assert "connect did not answer" in coordinator.state.last_error
        assert coordinator.state.last_sync is None

    asyncio.run(run())


def test_every_gatt_step_is_bounded_and_link_released(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    client = _HangingClient()

    async def connect(*args, **kwargs):
        return client

    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)
    monkeypatch.setattr(coordinator_mod, "STEP_TIMEOUT", 0.1)

    async def run():
        hass, entry = make_hass(), make_entry()
        await package.async_setup_entry(hass, entry)
        coordinator = hass.data["lywsd02"]["e1"]
        start = time.monotonic()
        await asyncio.wait_for(entry.tasks[0], 3)
        # read, acked write, unacked write, units read, verify read: all bounded.
        assert time.monotonic() - start < 1.5
        assert client.disconnected
        assert coordinator.state.last_error

    asyncio.run(run())


def test_entities_populate_when_data_arrives_after_setup(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    gate = asyncio.Event()
    client = _GoodClient()

    async def connect(*args, **kwargs):
        await gate.wait()  # the clock only answers after setup returned
        return client

    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)

    async def run():
        hass, entry = make_hass(), make_entry()
        await package.async_setup_entry(hass, entry)
        coordinator = hass.data["lywsd02"]["e1"]
        # Before data: nothing fabricated.
        assert coordinator.sync_age_hours is None
        assert coordinator.state.last_drift is None
        updates = coordinator.updates
        gate.set()
        await asyncio.wait_for(entry.tasks[0], 2)
        assert coordinator.sync_age_hours is not None
        assert coordinator.state.last_error is None
        assert coordinator.updates == updates + 1  # listeners were notified
        assert client.writes, "the due time sync was written once connected"

    asyncio.run(run())


def test_restored_state_shown_and_no_connection_when_sync_not_due(pkg, make_hass, make_entry, monkeypatch):
    package, coordinator_mod = pkg
    connect = AsyncMock()
    monkeypatch.setattr(coordinator_mod, "establish_connection", connect)
    coordinator_mod_state = coordinator_mod.ClockState(
        last_sync=datetime.now(timezone.utc), tz_offset_hours=0, units="°C"
    )
    sys.modules["homeassistant.helpers.storage"].Store.stored = coordinator_mod_state.as_dict()

    async def run():
        hass, entry = make_hass(), make_entry()
        await package.async_setup_entry(hass, entry)
        coordinator = hass.data["lywsd02"]["e1"]
        assert coordinator.state.units == "°C"  # restored, available at once
        assert coordinator.sync_age_hours is not None
        await asyncio.wait_for(entry.tasks[0], 2)
        # The clock being present or not never triggers a write by itself.
        connect.assert_not_called()

    asyncio.run(run())

