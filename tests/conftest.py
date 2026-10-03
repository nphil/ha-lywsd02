"""Minimal stand-ins for Home Assistant, bleak and bleak_retry_connector.

Home Assistant is not installed in the test environment (and must never be
touched), so the integration package is imported against small stubs that
reproduce only what the code under test uses. Everything else is a MagicMock.
"""

from __future__ import annotations
from datetime import datetime, timezone

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "lywsd02"

collect_ignore = ["../custom_components"]


class _Module(types.ModuleType):
    """A module whose unknown attributes are MagicMocks."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        value = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


class ConfigEntryNotReady(Exception):
    pass


class HassJob:
    def __init__(self, target, name=None) -> None:
        self.target = target
        self.name = name


class _Store:
    stored: dict | None = None

    def __init__(self, hass, version, key) -> None:
        self.saved: dict | None = None

    async def async_load(self):
        return _Store.stored

    async def async_save(self, data) -> None:
        self.saved = data


class _DataUpdateCoordinator:
    """Just enough of the real coordinator: data, listeners, async_refresh."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval=None) -> None:
        self.hass = hass
        self.name = name
        self.data = None
        self.last_update_success = True
        self.refresh_count = 0
        self.updates = 0

    async def async_refresh(self) -> None:
        self.refresh_count += 1
        self.data = await self._async_update_data()

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.updates += 1


class _CoordinatorEntity:
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls


def _install_stubs() -> None:
    names = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.exceptions",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.typing",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.storage",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.util",
        "homeassistant.util.dt",
        "bleak",
        "bleak.backends",
        "bleak.backends.device",
        "bleak_retry_connector",
    ]
    for name in names:
        sys.modules[name] = _Module(name)
    dt = sys.modules["homeassistant.util.dt"]
    dt.utcnow = lambda: datetime.now(timezone.utc)
    dt.now = lambda: datetime.now(timezone.utc)
    dt.parse_datetime = datetime.fromisoformat
    sys.modules["homeassistant.util"].dt = dt
    sys.modules["homeassistant.components"].bluetooth = MagicMock(name="bluetooth")
    sys.modules["homeassistant.helpers.storage"].Store = _Store
    uc = sys.modules["homeassistant.helpers.update_coordinator"]
    uc.DataUpdateCoordinator = _DataUpdateCoordinator
    uc.CoordinatorEntity = _CoordinatorEntity
    sys.modules["homeassistant.core"].HomeAssistant = object
    sys.modules["homeassistant.core"].HassJob = HassJob
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = ConfigEntryNotReady
    sys.modules["bleak_retry_connector"].BleakClientWithServiceCache = object
    sys.modules["bleak_retry_connector"].establish_connection = None  # per test

    spec = importlib.util.spec_from_file_location(
        "lywsd02",
        PACKAGE / "__init__.py",
        submodule_search_locations=[str(PACKAGE)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["lywsd02"] = module
    spec.loader.exec_module(module)


_install_stubs()


@pytest.fixture
def pkg():
    """The integration package plus its coordinator module."""
    _Store.stored = None
    return sys.modules["lywsd02"], sys.modules["lywsd02.coordinator"]


def _build_hass():
    from unittest.mock import AsyncMock

    hass = MagicMock()
    hass.data = {}
    hass.shutdown_jobs = []  # HassJob objects currently registered
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.services.has_service.return_value = True  # skip service registration

    def add_shutdown_job(job):
        hass.shutdown_jobs.append(job)
        return lambda: hass.shutdown_jobs.remove(job)

    hass.async_add_shutdown_job = add_shutdown_job
    return hass


def _build_entry():
    import asyncio

    entry = MagicMock()
    entry.entry_id = "e1"
    entry.title = "Clock"
    entry.data = {"address": "AA:BB:CC:DD:EE:FF"}
    entry.options = {}
    entry.tasks = []
    entry.unload_callbacks = []
    entry.async_on_unload = entry.unload_callbacks.append

    def create_background_task(hass, coro, name):
        task = asyncio.get_running_loop().create_task(coro, name=name)
        entry.tasks.append(task)
        return task

    entry.async_create_background_task = create_background_task
    return entry


@pytest.fixture
def make_hass():
    return _build_hass


@pytest.fixture
def make_entry():
    return _build_entry
