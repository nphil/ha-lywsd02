"""Shared entity base for the LYWSD02 clock."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import Lywsd02Coordinator


class Lywsd02Entity(CoordinatorEntity[Lywsd02Coordinator]):
    """Base entity: attaches to the device xiaomi_ble already owns."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Lywsd02Coordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = coordinator.device_info
