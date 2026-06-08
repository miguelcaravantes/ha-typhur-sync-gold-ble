"""The Typhur Sync Gold BLE integration."""

from __future__ import annotations

import importlib
import logging
import sys

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

# TODO: Remove _ensure_zstandard() and related imports once Home Assistant updates
# their wheel index (wheels.home-assistant.io) to include cp314 wheels for zstandard.
# When that happens, add "zstandard==0.25.0" back to manifest.json requirements
# and remove this workaround. See ZSTANDARD_ISSUE.md for details.
_ZSTANDARD_VERSION = "0.25.0"


def _ensure_zstandard() -> None:
    """Ensure zstandard is installed, installing it if missing.

    Workaround for HA 2026.3+ (Python 3.14) where the bundled pip index
    doesn't have cp314 wheels for zstandard.
    """
    try:
        importlib.import_module("zstandard")
    except ImportError:
        _LOGGER.info("Installing zstandard==%s", _ZSTANDARD_VERSION)
        import subprocess

        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", f"zstandard=={_ZSTANDARD_VERSION}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


_ensure_zstandard()
from .coordinator import TyphurDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Typhur Sync Gold BLE from a config entry."""
    coordinator = TyphurDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Typhur Sync Gold BLE config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: TyphurDataUpdateCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_shutdown()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry data."""
    return True
