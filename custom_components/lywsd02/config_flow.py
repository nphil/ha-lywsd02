"""Config and options flow for the LYWSD02 clock."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import (
    ADV_NAME_PREFIX,
    ADV_SERVICE_DATA_UUID,
    ADV_SERVICE_UUID,
    CONF_SYNC_INTERVAL,
    CONF_TOLERANCE,
    DEFAULT_SYNC_INTERVAL,
    DEFAULT_TOLERANCE,
    DOMAIN,
)


def _is_lywsd02(info: BluetoothServiceInfoBleak) -> bool:
    """LYWSD02 advertises the Mi service UUID and a LYWSD02-prefixed name.

    The UUID alone also matches LYWSD03MMC, whose time characteristic behaves
    differently, so the name prefix is load-bearing rather than cosmetic.
    """
    if not (info.name or "").upper().startswith(ADV_NAME_PREFIX):
        return False
    # Corroborate with the Xiaomi advertisement so a renamed third-party device
    # cannot match on name alone.
    uuids = {u.lower() for u in info.service_uuids}
    return ADV_SERVICE_DATA_UUID in {u.lower() for u in info.service_data} or ADV_SERVICE_UUID in uuids


class Lywsd02ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the clock."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._names: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_lywsd02(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovered = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered clock."""
        assert self._discovered is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered.name or self._discovered.address,
                data={CONF_ADDRESS: self._discovered.address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovered.name or self._discovered.address,
                "address": self._discovered.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick from the clocks currently visible to the proxies."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._names.get(address, address), data={CONF_ADDRESS: address}
            )

        current = self._async_current_ids()
        self._names = {
            info.address: f"{info.name} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_lywsd02(info) and info.address not in current
        }
        if not self._names:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._names)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> Lywsd02OptionsFlow:
        return Lywsd02OptionsFlow()


class Lywsd02OptionsFlow(OptionsFlow):
    """Sync cadence and how strict the read-back check is."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SYNC_INTERVAL,
                        default=opts.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                    vol.Required(
                        CONF_TOLERANCE,
                        default=opts.get(CONF_TOLERANCE, DEFAULT_TOLERANCE),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                }
            ),
        )
