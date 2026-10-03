"""A `dump_gatt` service, because this device's protocol is undocumented.

Every public description of the LYWSD02 protocol is reverse-engineered, and the
firmware revisions disagree with each other: the 12/24-hour "magic write" that
upstream integrations use is rejected outright by some units with
`Invalid attribute length`. When that happens the only way forward is to read the
device's own GATT table rather than trust a blog post, so that capability ships
with the integration instead of living in a throwaway script.
"""

from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, ServiceCall

from . import shutdown
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_DUMP_GATT = "dump_gatt"
SERVICE_WRITE_CHAR = "write_char"


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the diagnostic service once per HA start."""
    if hass.services.has_service(DOMAIN, SERVICE_DUMP_GATT):
        return

    async def _dump_gatt(call: ServiceCall) -> None:
        address: str = call.data["address"].upper()
        device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            _LOGGER.error("dump_gatt: %s is not reachable through a connectable proxy", address)
            return

        if shutdown.in_progress(hass):
            _LOGGER.info("dump_gatt: Home Assistant is shutting down; not connecting")
            return
        client = await establish_connection(BleakClientWithServiceCache, device, address)
        lines: list[str] = [f"GATT table for {address}:"]
        try:
            for service in client.services:
                lines.append(f"  service {service.uuid}")
                for char in service.characteristics:
                    props = ",".join(char.properties)
                    value = ""
                    if "read" in char.properties:
                        try:
                            raw = await client.read_gatt_char(char)
                            value = f" value={raw.hex()} len={len(raw)}"
                        except Exception as err:  # noqa: BLE001
                            value = f" value=<unreadable: {err}>"
                    lines.append(
                        f"    char {char.uuid} handle={char.handle} [{props}]{value}"
                    )
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

        _LOGGER.warning("\n".join(lines))

    hass.services.async_register(DOMAIN, SERVICE_DUMP_GATT, _dump_gatt)


async def async_register_write_service(hass: HomeAssistant) -> None:
    """Register the raw-write probe.

    Sharp on purpose. This device's display settings are undocumented, and the
    only way to identify a register is to write a candidate value and look at
    the e-ink face. The service logs the value before and after, so a probe is
    always reversible.
    """
    if hass.services.has_service(DOMAIN, SERVICE_WRITE_CHAR):
        return

    async def _write_char(call: ServiceCall) -> None:
        address: str = call.data["address"].upper()
        uuid: str = call.data["uuid"].lower()
        payload = bytes.fromhex(call.data["payload"].replace(" ", ""))

        device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            _LOGGER.error("write_char: %s is not reachable", address)
            return

        if shutdown.in_progress(hass):
            _LOGGER.info("write_char: Home Assistant is shutting down; not connecting")
            return
        client = await establish_connection(BleakClientWithServiceCache, device, address)
        try:
            try:
                before = (await client.read_gatt_char(uuid)).hex()
            except Exception as err:  # noqa: BLE001
                before = f"<unreadable: {err}>"
            await client.write_gatt_char(uuid, payload, response=True)
            try:
                after = (await client.read_gatt_char(uuid)).hex()
            except Exception as err:  # noqa: BLE001
                after = f"<unreadable: {err}>"
            _LOGGER.warning(
                "write_char %s %s: wrote %s | before=%s after=%s",
                address, uuid, payload.hex(), before, after,
            )
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    hass.services.async_register(DOMAIN, SERVICE_WRITE_CHAR, _write_char)
