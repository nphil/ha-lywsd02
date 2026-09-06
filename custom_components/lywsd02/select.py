"""The device setting this firmware actually accepts.

A 12/24-hour select is deliberately absent. Upstream integrations write a 7-byte
payload to the time characteristic for that, but this clock's time characteristic
is strictly 5 bytes (verified by reading its own GATT table on firmware
1.1.2_0097 - handle 62, len 5), so the write returns GATT error 13,
`Invalid attribute length`. Shipping a control that always errors is worse than
not shipping one; see README "Protocol notes" for the candidate registers.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, UNITS
from .coordinator import Lywsd02Coordinator
from .entity import Lywsd02Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Lywsd02Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DisplayUnitsSelect(coordinator)])


class DisplayUnitsSelect(Lywsd02Entity, SelectEntity):
    """Celsius / Fahrenheit on the clock's own display.

    This one IS readable, so its state is the device's answer rather than an
    assumption, and it is refreshed on every sync. It changes only what the
    e-ink face shows; HA's own temperature sensor comes from xiaomi_ble and is
    unaffected.
    """

    _attr_name = "Display Units"
    _attr_options = UNITS
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: Lywsd02Coordinator) -> None:
        super().__init__(coordinator, "display_units")

    @property
    def available(self) -> bool:
        return self.coordinator.reachable

    @property
    def current_option(self) -> str | None:
        return self.coordinator.state.units

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_sync(reason="display units", units=option)
