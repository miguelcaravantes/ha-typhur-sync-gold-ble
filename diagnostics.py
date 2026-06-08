"""Diagnostics for Typhur Sync Gold BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_USER_ID, DOMAIN
from .coordinator import TyphurDataUpdateCoordinator

TO_REDACT = {CONF_ADDRESS, CONF_USER_ID, "deviceId", "deviceSn", "cookUuid", "userId"}


def _redact_nested(value: Any) -> Any:
    """Redact sensitive values recursively."""
    if isinstance(value, dict):
        return {
            key: "**REDACTED**" if key in TO_REDACT else _redact_nested(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: TyphurDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    data = coordinator.data if coordinator else None
    return {
        "entry": async_redact_data(entry.data, TO_REDACT),
        "device_type": coordinator.device_type if coordinator else None,
        "probe_count": coordinator.probe_count if coordinator else None,
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "last_status": _redact_nested(data.raw) if data else None,
    }
