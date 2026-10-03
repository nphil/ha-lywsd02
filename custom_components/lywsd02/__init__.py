"""Xiaomi LYWSD02 clock: time sync and the settings the Mi Home app hides.

Passive temperature/humidity/battery already come from core `xiaomi_ble`. This
integration only does the things that require an actual connection, and it does
them through whichever ESPHome Bluetooth proxy HA considers best - so it is not
tied to any one node.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import Lywsd02Coordinator
from .diagnostics_service import async_register_services, async_register_write_service

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a clock from a config entry."""
    coordinator = Lywsd02Coordinator(hass, entry)
    await coordinator.async_load()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_register_services(hass)
    await async_register_write_service(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

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
