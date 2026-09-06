"""Constants and wire protocol for the Xiaomi LYWSD02 e-ink clock.

Protocol sources (they disagree, so each claim below is attributed):
  * Characteristic table: whoisnotthere/LYWSD02-Reading-and-changing-data
  * Reference implementation: h4/lywsd02 (bluepy)
  * The 12/24-hour magic write: ashald/home-assistant-lywsd02

Everything lives under one service, EBE0CCB0-7A0A-4B0C-8A1A-6FF2997DA3A6.
"""

from __future__ import annotations

import struct
from typing import Final

DOMAIN: Final = "lywsd02"

# The GATT service every characteristic below lives under. NOTE: this UUID is
# only visible AFTER connecting - it never appears in an advertisement, so it is
# useless for discovery. Matching on it silently finds nothing.
SERVICE_UUID: Final = "ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6"

# What the clock actually broadcasts: MiBeacon service data, Environmental
# Sensing, and the Xiaomi DFU service. Verified live 2026-09-06:
#   name "LYWSD02", service_data {0000fe95...}, service_uuids [181a, fef5]
ADV_NAME_PREFIX: Final = "LYWSD02"
ADV_SERVICE_DATA_UUID: Final = "0000fe95-0000-1000-8000-00805f9b34fb"
ADV_SERVICE_UUID: Final = "0000181a-0000-1000-8000-00805f9b34fb"

# 5 bytes, READ + WRITE: uint32 LE epoch, then a signed tz byte in whole hours.
# The display shows epoch + tz*3600, so the device itself is tz-naive.
CHAR_TIME: Final = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"

# 1 byte, READ + WRITE: 0x01 = Fahrenheit, 0xFF = Celsius.
# h4/lywsd02's inline comment claims "0x00 - F, 0x01 - C" but its own UNITS map
# uses 0x01/0xFF, which is what the protocol README documents. Trust the map.
CHAR_UNITS: Final = "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6"


UNIT_CELSIUS: Final = "°C"
UNIT_FAHRENHEIT: Final = "°F"
UNITS: Final = [UNIT_CELSIUS, UNIT_FAHRENHEIT]

CONF_SYNC_INTERVAL: Final = "sync_interval_hours"
CONF_TOLERANCE: Final = "drift_tolerance_seconds"
DEFAULT_SYNC_INTERVAL: Final = 24
DEFAULT_TOLERANCE: Final = 5

# How often we wake up to decide whether a sync is due. Short enough that a DST
# transition is corrected promptly, long enough to be free when nothing is due.
CHECK_INTERVAL_MINUTES: Final = 30

STORAGE_VERSION: Final = 1


def encode_time(epoch: int, tz_hours: int) -> bytes:
    """Build the 5-byte time payload."""
    return struct.pack("<Ib", epoch, tz_hours)


def decode_time(raw: bytes) -> tuple[int, int]:
    """Parse the time characteristic; older firmware omits the tz byte."""
    if len(raw) >= 5:
        epoch, tz_hours = struct.unpack("<Ib", raw[:5])
        return epoch, tz_hours
    if len(raw) == 4:
        return struct.unpack("<I", raw)[0], 0
    raise ValueError(f"time characteristic returned {len(raw)} bytes: {raw!r}")


def encode_units(unit: str) -> bytes:
    """Build the temperature-unit payload."""
    return b"\x01" if unit == UNIT_FAHRENHEIT else b"\xff"


def decode_units(raw: bytes) -> str | None:
    """Parse the temperature-unit characteristic."""
    if not raw:
        return None
    return UNIT_FAHRENHEIT if raw[0] == 0x01 else UNIT_CELSIUS
