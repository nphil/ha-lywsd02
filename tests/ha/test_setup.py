"""Real Home Assistant: the entry loads, the device and entities register, unload is clean."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.lywsd02.const import DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"address": ADDRESS}, title="LYWSD02", unique_id=ADDRESS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_setup_registers_device_and_entities_then_unloads(
    hass: HomeAssistant, mock_bluetooth: None
) -> None:
    """No clock in range: setup still succeeds, with the device and four entities present."""
    entry = await _setup(hass)

    devices = dr.async_get(hass).async_get_devices(identifiers={(DOMAIN, ADDRESS)})
    assert len(devices) == 1
    assert devices[0].manufacturer == "Xiaomi"
    assert devices[0].model == "LYWSD02"
    assert (dr.CONNECTION_BLUETOOTH, ADDRESS.lower()) in devices[0].connections or (
        dr.CONNECTION_BLUETOOTH,
        ADDRESS,
    ) in devices[0].connections

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.domain for e in entities} == {"button", "select", "sensor"}
    assert len(entities) == 4

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_device_adopts_the_name_of_a_sibling_device(
    hass: HomeAssistant, mock_bluetooth: None
) -> None:
    """The clock takes the name another integration (xiaomi_ble) gave the same Bluetooth address."""
    other = MockConfigEntry(domain="xiaomi_ble", data={}, title="Other")
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name="Bedroom Clock",
    )

    entry = await _setup(hass)

    devices = dr.async_get(hass).async_get_devices(identifiers={(DOMAIN, ADDRESS)})
    assert len(devices) == 1 and devices[0].name == "Bedroom Clock"
    names = {
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert "sensor.bedroom_clock_sync_age" in names
