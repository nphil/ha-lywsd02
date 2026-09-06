"""Manual time-sync button for the LYWSD02 clock."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Lywsd02Coordinator
from .entity import Lywsd02Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Lywsd02Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TimeSyncButton(coordinator)])


class TimeSyncButton(Lywsd02Entity, ButtonEntity):
    """Force a time write now, regardless of when the last one happened."""

    _attr_name = "Time Sync"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: Lywsd02Coordinator) -> None:
        super().__init__(coordinator, "time_sync")

    @property
    def available(self) -> bool:
        # A write needs a connectable route; pressing while the clock is out of
        # range would just log a failure.
        return self.coordinator.reachable

    async def async_press(self) -> None:
        await self.coordinator.async_sync(reason="button")
