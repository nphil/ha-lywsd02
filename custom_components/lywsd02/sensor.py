"""Sync-health sensors for the LYWSD02 clock."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Lywsd02Coordinator
from .entity import Lywsd02Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Lywsd02Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SyncAgeSensor(coordinator), DriftSensor(coordinator)])


class SyncAgeSensor(Lywsd02Entity, SensorEntity):
    """Hours since the last VERIFIED time write.

    Deliberately always available and deliberately without a state_class:

    * Always available, because this is HA-side derived state. If the clock
      falls out of proxy range the number must keep climbing - that rising value
      is what a watchdog automation triggers on. Going `unavailable` here would
      hide a dead clock instead of reporting it.
    * No state_class, so it generates no long-term statistics. A sawtooth that
      resets to zero on every sync has no meaningful hourly mean.
    """

    _attr_name = "Sync Age"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(self, coordinator: Lywsd02Coordinator) -> None:
        super().__init__(coordinator, "sync_age")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float | None:
        return self.coordinator.sync_age_hours

    @property
    def extra_state_attributes(self) -> dict:
        state = self.coordinator.state
        return {
            "last_sync": state.last_sync.isoformat() if state.last_sync else None,
            "last_error": state.last_error,
            "reachable": self.coordinator.reachable,
        }


class DriftSensor(Lywsd02Entity, SensorEntity):
    """Seconds the clock was wrong by, measured BEFORE the last correction.

    This is the only honest measure of whether the clock keeps time: after a
    write it is zero by construction, so it is sampled on connect, before the
    new timestamp goes out.
    """

    _attr_name = "Drift"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-fast"

    def __init__(self, coordinator: Lywsd02Coordinator) -> None:
        super().__init__(coordinator, "drift")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float | None:
        return self.coordinator.state.last_drift
