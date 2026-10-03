"""Connect-on-demand coordinator for the LYWSD02 clock."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import shutdown
from .const import (
    CHAR_TIME,
    CHAR_UNITS,
    CHECK_INTERVAL_MINUTES,
    CONF_SYNC_INTERVAL,
    CONF_TOLERANCE,
    DEFAULT_SYNC_INTERVAL,
    DEFAULT_TOLERANCE,
    DOMAIN,
    STORAGE_VERSION,
    decode_time,
    decode_units,
    encode_time,
    encode_units,
)

_LOGGER = logging.getLogger(__name__)

# No single connect / read / write / disconnect may hang longer than this, even
# though bleak and bleak_retry_connector have no ceiling of their own for
# several of those calls (startup contract S4). This is a cancellation-based
# safety net: bleak's read/write take no timeout argument and
# establish_connection hard-codes its own per-attempt connect timeout, so there
# is no shorter backend timeout to pass (S8). The integration never subscribes
# to notifications, which is the call S8 is chiefly about.
STEP_TIMEOUT = 10.0


@dataclass(frozen=True)
class ClockState:
    """Everything the entities render. Persisted across restarts."""

    last_sync: datetime | None = None
    last_drift: float | None = None
    last_error: str | None = None
    units: str | None = None  # readable, so this is the device's own answer
    tz_offset_hours: int | None = None

    def as_dict(self) -> dict:
        return {
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_drift": self.last_drift,
            "last_error": self.last_error,
            "units": self.units,
            "tz_offset_hours": self.tz_offset_hours,
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> ClockState:
        if not raw:
            return cls()
        ts = raw.get("last_sync")
        return cls(
            last_sync=dt_util.parse_datetime(ts) if ts else None,
            last_drift=raw.get("last_drift"),
            last_error=raw.get("last_error"),
            units=raw.get("units"),
            tz_offset_hours=raw.get("tz_offset_hours"),
        )


class _Latched(Exception):
    """Home Assistant began shutting down while a sync was connecting."""


class Lywsd02Coordinator(DataUpdateCoordinator[ClockState]):
    """Owns the BLE conversation. Connects only when there is something to do.

    The device is tz-naive: it renders `epoch + tz*3600`. We therefore write a
    real UTC epoch plus the local offset, and fold any sub-hour remainder (India,
    Newfoundland) into the epoch, because the tz byte only holds whole hours.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.address: str = entry.data["address"]
        self.entry = entry
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._state = ClockState()
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.address}",
            update_interval=timedelta(minutes=CHECK_INTERVAL_MINUTES),
        )
        self._client: BleakClientWithServiceCache | None = None
        self._inflight: set[asyncio.Task] = set()

    @property
    def sync_interval(self) -> timedelta:
        return timedelta(
            hours=self.entry.options.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL)
        )

    @property
    def tolerance(self) -> float:
        return float(self.entry.options.get(CONF_TOLERANCE, DEFAULT_TOLERANCE))

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this clock, borrowing the name of any sibling device row.

        Home Assistant no longer merges devices across config entries: as of the
        2026 registry, `async_get_or_create` looks devices up with
        `config_entry_id=` in the query, and previously-merged devices were SPLIT
        into one row per entry (`composite_device_id` survives only as a
        back-reference so old automations still resolve). Verified against
        homeassistant/helpers/device_registry.py on 2026-09-06.

        So a second integration on the same physical device ALWAYS gets its own
        row - passing matching identifiers or connections does not change that.
        What it does change is the name, and the name is what every entity id is
        derived from. We therefore look the clock up unscoped, adopt whatever the
        operator called it, and inherit their naming convention for free.
        """
        name: str | None = None
        registry = dr.async_get(self.hass)
        for device in registry.async_get_devices(
            connections={(CONNECTION_BLUETOOTH, self.address)}
        ):
            if device.config_entry_id == self.entry.entry_id:
                continue
            name = device.name_by_user or device.name
            if name:
                break

        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            connections={(CONNECTION_BLUETOOTH, self.address)},
            name=name,
            manufacturer="Xiaomi",
            model="LYWSD02",
        )

    async def async_load(self) -> None:
        self._state = ClockState.from_dict(await self._store.async_load())
        # Entities render from `state`; seeding `data` lets setup finish without
        # waiting for a first refresh (which may need a radio connection).
        self.data = self._state

    async def _async_save(self) -> None:
        await self._store.async_save(self._state.as_dict())

    def _current_offset_hours(self) -> int:
        offset = dt_util.now().utcoffset() or timedelta(0)
        return int(offset.total_seconds() // 3600)

    def _sync_due(self) -> tuple[bool, str]:
        if self._state.last_sync is None:
            return True, "never synced"
        offset = self._current_offset_hours()
        if self._state.tz_offset_hours is not None and offset != self._state.tz_offset_hours:
            return True, f"utc offset changed {self._state.tz_offset_hours}h -> {offset}h"
        age = dt_util.utcnow() - self._state.last_sync
        if age >= self.sync_interval:
            return True, f"last sync {age.total_seconds() / 3600:.1f}h ago"
        return False, ""

    async def _async_update_data(self) -> ClockState:
        """Scheduled tick. Never raises: a failed sync must not blank entities.

        A sync failure deliberately leaves `last_sync` stale so the sync-age
        sensor keeps climbing and the operator's watchdog automation fires. That
        is the whole alerting mechanism - swallowing it here would hide a dead
        clock behind an 'unavailable' entity.
        """
        if shutdown.in_progress(self.hass):
            return self._state
        due, why = self._sync_due()
        if due:
            _LOGGER.debug("%s: sync due (%s)", self.address, why)
            await self.async_sync(reason=why)
        return self._state

    async def _bounded(self, step: str, awaitable):
        """Await one BLE step, failing after STEP_TIMEOUT instead of hanging."""
        try:
            async with asyncio.timeout(STEP_TIMEOUT):
                return await awaitable
        except TimeoutError as err:
            raise TimeoutError(
                f"{step} did not answer within {STEP_TIMEOUT:g} s"
            ) from err

    def _ble_device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise TimeoutError(
                f"{self.address} not reachable through any connectable proxy"
            )
        return device

    async def async_sync(
        self, *, reason: str = "manual", units: str | None = None
    ) -> None:
        """Run one sync unless Home Assistant is shutting down.

        The calling task is tracked so the shutdown job can cancel a sync that
        is mid-connection; a refusal while latched is not a failure.
        """
        if shutdown.in_progress(self.hass):
            return
        task = asyncio.current_task()
        self._inflight.add(task)
        try:
            await self._async_sync(reason=reason, units=units)
        finally:
            self._inflight.discard(task)

    async def _disconnect(self, client: BleakClientWithServiceCache | None) -> None:
        """Disconnect one client, bounded, and even if the caller is cancelled.

        The disconnect runs as its own task behind a shield, so cancelling the
        sync task cannot abandon an open link. Never raises.
        """
        if client is None:
            return
        if self._client is client:
            self._client = None
        task = asyncio.ensure_future(client.disconnect())
        try:
            async with asyncio.timeout(STEP_TIMEOUT):
                await asyncio.shield(task)
        except Exception:  # noqa: BLE001 - includes the timeout; best effort
            pass

    async def async_release(self) -> None:
        """Shutdown: stop any sync in flight and drop any link still held."""
        current = asyncio.current_task()
        tasks = [t for t in self._inflight if t is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._disconnect(self._client)

    async def _async_sync(
        self, *, reason: str = "manual", units: str | None = None
    ) -> None:
        """One connection: read drift, apply any setting, set the time, verify.

        The time write always goes last so it is the authoritative one.
        """
        target_units = units or None
        offset_hours = self._current_offset_hours()
        drift: float | None = None
        error: str | None = None
        setting_errors: list[str] = []
        client: BleakClientWithServiceCache | None = None
        latched = False

        try:
            device = self._ble_device()
            client = await self._bounded(
                "connect",
                establish_connection(BleakClientWithServiceCache, device, self.address),
            )
            self._client = client
            if shutdown.in_progress(self.hass):
                raise _Latched

            # 1. Measure drift BEFORE correcting: a large value here is the
            #    evidence that the clock was actually wrong, and a small one
            #    proves the previous write's byte order was right.
            try:
                epoch, tz_read = decode_time(
                    await self._bounded("read time", client.read_gatt_char(CHAR_TIME))
                )
                shown = epoch + tz_read * 3600
                drift = float(shown - self._target_display_epoch())
            except Exception as err:  # noqa: BLE001 - read is best-effort
                _LOGGER.debug("%s: could not read clock before write: %s", self.address, err)

            # 2. Optional settings, then 3. the authoritative time write.
            # Optional settings are best-effort ON PURPOSE. Firmware revisions
            # disagree about these writes - one rejects the 12/24-hour payload
            # with "Invalid attribute length" - and a rejected *setting* must
            # never cost us the time write, which is this integration's job.
            if units is not None:
                try:
                    await self._bounded(
                        "write units",
                        client.write_gatt_char(
                            CHAR_UNITS, encode_units(units), response=True
                        ),
                    )
                except Exception as err:  # noqa: BLE001
                    setting_errors.append(f"units not accepted: {err}")

            now = dt_util.utcnow()
            residual = int((dt_util.now().utcoffset() or timedelta(0)).total_seconds()) - offset_hours * 3600
            payload = encode_time(int(now.timestamp()) + residual, offset_hours)
            # koenvervloesem/bluetooth-clocks sets WRITE_WITH_RESPONSE = False for
            # this model ("needs write without response"). Firmware 1.1.2_0097
            # accepts an acked write fine, and an ack is worth having, so try
            # that first and fall back rather than assuming either way.
            try:
                await self._bounded(
                    "write time",
                    client.write_gatt_char(CHAR_TIME, payload, response=True),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "%s: acked time write refused (%s); retrying unacked",
                    self.address, err,
                )
                await self._bounded(
                    "write time (unacked)",
                    client.write_gatt_char(CHAR_TIME, payload, response=False),
                )

            # 4. Read the unit back - unlike the clock mode, it is readable, so
            #    this is the device's answer rather than our assumption.
            try:
                target_units = decode_units(
                    await self._bounded("read units", client.read_gatt_char(CHAR_UNITS))
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("%s: unit read-back failed: %s", self.address, err)
                target_units = units or self._state.units

            # 5. Verify: a write ack only proves five bytes were accepted, not
            #    that they meant what we intended.
            verified_drift: float | None = None
            try:
                epoch, tz_read = decode_time(
                    await self._bounded("read-back", client.read_gatt_char(CHAR_TIME))
                )
                verified_drift = float((epoch + tz_read * 3600) - self._target_display_epoch())
            except Exception as err:  # noqa: BLE001
                error = f"read-back failed: {err}"

            if verified_drift is None:
                error = error or "read-back returned nothing"
            elif abs(verified_drift) > self.tolerance:
                error = f"read-back disagreed by {verified_drift:.0f}s"
            else:
                self._state = replace(
                    self._state,
                    last_sync=dt_util.utcnow(),
                    last_drift=drift if drift is not None else verified_drift,
                    last_error=None,
                    units=target_units,
                    tz_offset_hours=offset_hours,
                )
                _LOGGER.info(
                    "%s: time synced (%s); drift before write %s s",
                    self.address,
                    reason,
                    "unknown" if drift is None else f"{drift:.0f}",
                )
        except _Latched:
            latched = True
        except Exception as err:  # noqa: BLE001 - reported, never raised
            error = str(err)
        finally:
            await self._disconnect(client)

        if latched:
            # Shutdown refusals are not faults: no error recorded, nothing saved.
            return

        if error is None and setting_errors:
            # The clock is right; a knob was refused. Surface it without
            # pretending the sync failed.
            error = "; ".join(setting_errors)
            _LOGGER.warning("%s: %s", self.address, error)
            self._state = replace(self._state, last_error=error)
        elif error is not None:
            _LOGGER.warning("%s: time sync failed (%s): %s", self.address, reason, error)
            self._state = replace(self._state, last_error=error, last_drift=drift)

        await self._async_save()
        self.async_set_updated_data(self._state)

    def _target_display_epoch(self) -> int:
        """What the clock's face should read, expressed as an epoch."""
        offset = int((dt_util.now().utcoffset() or timedelta(0)).total_seconds())
        return int(dt_util.utcnow().timestamp()) + offset

    @property
    def state(self) -> ClockState:
        return self._state

    @property
    def sync_age_hours(self) -> float | None:
        if self._state.last_sync is None:
            return None
        return (dt_util.utcnow() - self._state.last_sync).total_seconds() / 3600

    @property
    def reachable(self) -> bool:
        return bluetooth.async_address_present(self.hass, self.address, connectable=True)
