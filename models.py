"""Typed protocol models for Typhur Sync Gold BLE devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import DEVICE_TYPE_MODEL_NAMES, DEVICE_TYPE_PROBE_COUNTS


def fahrenheit_x10_to_native(value: int | float | None) -> float | None:
    """Convert a Fahrenheit x 10 protocol value to Fahrenheit."""
    if value is None:
        return None
    return value / 10


@dataclass(slots=True, frozen=True)
class ProbeStatus:
    """Status for a single Typhur probe."""

    index: int
    device_sn: str | None = None
    probe_color: str | None = None
    cooking_state: str | None = None
    battery_value: int | None = None
    engaged_status: str | bool | None = None
    cur_temperature: int | None = None
    area_temperature: tuple[int | None, ...] = field(default_factory=tuple)
    cur_ambient_temperature: int | None = None
    temperature_percentile: tuple[int | None, ...] = field(default_factory=tuple)
    cooking_mode: str | None = None
    cook_uuid: str | None = None
    start_client: str | None = None
    set_params: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    total_cook_sec: int | None = None
    cur_cook_sec: int | None = None
    cur_remained_sec: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_key(self) -> str:
        """Return a stable key for entity IDs."""
        return self.probe_color or f"probe{self.index + 1}"

    @property
    def core_temperature(self) -> float | None:
        """Return current core temperature in Fahrenheit."""
        return fahrenheit_x10_to_native(self.cur_temperature)

    @property
    def ambient_temperature(self) -> float | None:
        """Return current ambient temperature in Fahrenheit."""
        return fahrenheit_x10_to_native(self.cur_ambient_temperature)

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature in Fahrenheit."""
        if not self.set_params:
            return None
        value = self.set_params[0].get("setTemperature")
        if isinstance(value, int | float):
            return fahrenheit_x10_to_native(value)
        return None

    def area_temperature_value(self, index: int) -> float | None:
        """Return an area temperature by zero-based index."""
        if index >= len(self.area_temperature):
            return None
        return fahrenheit_x10_to_native(self.area_temperature[index])

    @property
    def engaged_status_text(self) -> str | None:
        """Return the device-reported engaged status as text."""
        if self.engaged_status is None:
            return None
        if isinstance(self.engaged_status, bool):
            return "true" if self.engaged_status else "false"
        return self.engaged_status


@dataclass(slots=True, frozen=True)
class BaseStationStatus:
    """Status for a Typhur Sync Gold base station."""

    device_type: str | None = None
    probe_count: int = 0
    user_id: str | None = None
    app_version: str | None = None
    control_board_version: str | None = None
    global_status: str | None = None
    battery_status: str | None = None
    battery_value: int | None = None
    wifi_rssi: int | None = None
    volume: str | None = None
    connect_status: str | None = None
    has_wifi_config: bool | None = None
    last_set_temperature: int | None = None
    probes: tuple[ProbeStatus, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def model_name(self) -> str | None:
        """Return a friendly model name."""
        if self.device_type is None:
            return None
        return DEVICE_TYPE_MODEL_NAMES.get(self.device_type, self.device_type)

    @property
    def discovered_probe_count(self) -> int:
        """Return the best known probe count."""
        if self.probe_count:
            return self.probe_count
        if self.device_type in DEVICE_TYPE_PROBE_COUNTS:
            return DEVICE_TYPE_PROBE_COUNTS[self.device_type]
        return len(self.probes)

    @property
    def last_set_temperature_native(self) -> float | None:
        """Return last set temperature in Fahrenheit."""
        return fahrenheit_x10_to_native(self.last_set_temperature)
