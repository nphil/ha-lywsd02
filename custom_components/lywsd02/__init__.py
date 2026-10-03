"""Xiaomi LYWSD02 clock: time sync and the settings the Mi Home app hides.

Passive temperature/humidity/battery already come from core `xiaomi_ble`. This
integration only does the things that require an actual connection, and it does
them through whichever ESPHome Bluetooth proxy HA considers best - so it is not
tied to any one node.

Home Assistant does not unload entries when it shuts down, so each clock
registers one shutdown job (Stage 1, while Bluetooth is still alive) that stops
any sync in flight and drops a link it still holds; a domain-lifetime job sets
the latch even if an entry has been unloaded by then.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import shutdown
from .const import DOMAIN
from .coordinator import Lywsd02Coordinator
from .diagnostics_service import async_register_services, async_register_write_service

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR]

#: Bound for releasing one clock's link in Home Assistant's shutdown stage (all jobs share 20 s).
SHUTDOWN_RELEASE_TIMEOUT = 8

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the shutdown latch once per Home Assistant run (never removed on unload)."""

    async def _async_shutdown_latch() -> None:
        shutdown.begin(hass)

    hass.async_add_shutdown_job(HassJob(_async_shutdown_latch, f"{DOMAIN} shutdown latch"))
    return True


def _refuse_while_shutting_down(hass: HomeAssistant) -> None:
    if shutdown.in_progress(hass):
        raise ConfigEntryNotReady("Home Assistant is shutting down")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a clock from a config entry."""
    _refuse_while_shutting_down(hass)
    coordinator = Lywsd02Coordinator(hass, entry)

    async def _async_release_at_shutdown() -> None:
        """Drop the clock's link while Bluetooth is still alive; never raises."""
        started = monotonic()
        shutdown.begin(hass)
        try:
            async with asyncio.timeout(SHUTDOWN_RELEASE_TIMEOUT):
                await coordinator.async_release()
        except Exception as err:  # noqa: BLE001 - includes the timeout; a shutdown job must never raise
            _LOGGER.warning(
                "Releasing the BLE link to %s at shutdown failed after %.2f s: %s",
                entry.title,
                monotonic() - started,
                err or type(err).__name__,
            )
        else:
            _LOGGER.info(
                "Released BLE link to %s at shutdown in %.2f s",
                entry.title,
                monotonic() - started,
            )

    # Registered before any further await (platform forwarding, store load).
    entry.async_on_unload(
        hass.async_add_shutdown_job(
            HassJob(_async_release_at_shutdown, f"{DOMAIN} release BLE link {entry.title}")
        )
    )

    await coordinator.async_load()
    _refuse_while_shutting_down(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_register_services(hass)
    await async_register_write_service(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    if shutdown.in_progress(hass):
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady("Home Assistant is shutting down")

    # Deliberately not syncing during setup: startup is when the proxies are
    # still coming up. The first check (sync if due) runs in a background task
    # owned by the entry, so setup returns at once however slow or absent the
    # clock is; entities show the stored last-known state meanwhile, and the
    # coordinator then keeps its own 30-minute schedule.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} first check {entry.title}"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
