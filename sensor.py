"""Sensor entities for Typhur Sync Gold BLE."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TyphurDataUpdateCoordinator
from .entity import TyphurEntity, TyphurProbeEntity
from .models import BaseStationStatus, ProbeStatus


@dataclass(frozen=True, kw_only=True)
class TyphurSensorEntityDescription(SensorEntityDescription):
    """Describe a Typhur sensor."""

    value_fn: Callable[[BaseStationStatus], str | int | float | None]
    options: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class TyphurProbeSensorEntityDescription(SensorEntityDescription):
    """Describe a Typhur probe sensor."""

    value_fn: Callable[[ProbeStatus], str | int | float | None]
    options: list[str] | None = None


BASE_SENSOR_DESCRIPTIONS: tuple[TyphurSensorEntityDescription, ...] = (
    TyphurSensorEntityDescription(
        key="base_battery",
        translation_key="base_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.battery_value,
    ),
    TyphurSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.wifi_rssi,
    ),
    TyphurSensorEntityDescription(
        key="last_set_temperature",
        translation_key="last_set_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.last_set_temperature_native,
    ),
    TyphurSensorEntityDescription(
        key="battery_status",
        translation_key="battery_status",
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "charged", "charging"],
        value_fn=lambda status: status.battery_status,
    ),
    TyphurSensorEntityDescription(
        key="volume",
        translation_key="volume",
        value_fn=lambda status: status.volume,
    ),
    TyphurSensorEntityDescription(
        key="connection_status",
        translation_key="connection_status",
        value_fn=lambda status: status.connect_status,
    ),
)

PROBE_SENSOR_DESCRIPTIONS: tuple[TyphurProbeSensorEntityDescription, ...] = (
    TyphurProbeSensorEntityDescription(
        key="core_temperature",
        translation_key="probe_core_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.core_temperature,
    ),
    TyphurProbeSensorEntityDescription(
        key="ambient_temperature",
        translation_key="probe_ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.ambient_temperature,
    ),
    TyphurProbeSensorEntityDescription(
        key="probe_battery",
        translation_key="probe_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.battery_value,
    ),
    TyphurProbeSensorEntityDescription(
        key="target_temperature",
        translation_key="probe_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.target_temperature,
    ),
    TyphurProbeSensorEntityDescription(
        key="cooking_state",
        translation_key="probe_cooking_state",
        value_fn=lambda probe: probe.cooking_state,
    ),
    TyphurProbeSensorEntityDescription(
        key="cooking_mode",
        translation_key="probe_cooking_mode",
        value_fn=lambda probe: probe.cooking_mode,
    ),
    TyphurProbeSensorEntityDescription(
        key="engaged_status",
        translation_key="probe_engaged_status",
        value_fn=lambda probe: probe.engaged_status_text,
    ),
    TyphurProbeSensorEntityDescription(
        key="current_cook_time",
        translation_key="probe_current_cook_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.cur_cook_sec,
    ),
    TyphurProbeSensorEntityDescription(
        key="remaining_cook_time",
        translation_key="probe_remaining_cook_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe: probe.cur_remained_sec,
    ),
)

AREA_SENSOR_DESCRIPTIONS: tuple[TyphurProbeSensorEntityDescription, ...] = tuple(
    TyphurProbeSensorEntityDescription(
        key=f"area_temperature_{index + 1}",
        translation_key=f"probe_area_temperature_{index + 1}",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda probe, area_index=index: probe.area_temperature_value(area_index),
    )
    for index in range(5)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Typhur sensors."""
    coordinator: TyphurDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_probes = 0
    entities: list[SensorEntity] = [
        TyphurBaseSensor(coordinator, description)
        for description in BASE_SENSOR_DESCRIPTIONS
    ]

    @callback
    def _async_add_discovered_probes() -> None:
        nonlocal added_probes
        target_probes = coordinator.probe_count or (
            coordinator.data.discovered_probe_count if coordinator.data else 0
        )
        if target_probes <= added_probes:
            return
        new_entities: list[SensorEntity] = []
        for probe_index in range(added_probes, target_probes):
            new_entities.extend(
                TyphurProbeSensor(coordinator, probe_index, description)
                for description in PROBE_SENSOR_DESCRIPTIONS + AREA_SENSOR_DESCRIPTIONS
            )
        added_probes = target_probes
        async_add_entities(new_entities)

    initial_probes = coordinator.probe_count or (
        coordinator.data.discovered_probe_count if coordinator.data else 0
    )
    for probe_index in range(initial_probes):
        entities.extend(
            TyphurProbeSensor(coordinator, probe_index, description)
            for description in PROBE_SENSOR_DESCRIPTIONS + AREA_SENSOR_DESCRIPTIONS
        )
    added_probes = initial_probes
    async_add_entities(entities)
    entry.async_on_unload(coordinator.async_add_listener(_async_add_discovered_probes))


class TyphurBaseSensor(TyphurEntity, SensorEntity):
    """Base station sensor."""

    entity_description: TyphurSensorEntityDescription

    def __init__(
        self,
        coordinator: TyphurDataUpdateCoordinator,
        description: TyphurSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_suggested_object_id = f"typhur_sync_gold_base_station_{description.key}"

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def options(self) -> list[str] | None:
        """Return enum options."""
        return self.entity_description.options


class TyphurProbeSensor(TyphurProbeEntity, SensorEntity):
    """Probe sensor."""

    entity_description: TyphurProbeSensorEntityDescription

    def __init__(
        self,
        coordinator: TyphurDataUpdateCoordinator,
        probe_index: int,
        description: TyphurProbeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, probe_index)
        self.entity_description = description
        self._attr_translation_placeholders = {"probe": str(probe_index + 1)}
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_probe_{probe_index + 1}_{description.key}"
        )
        self._attr_suggested_object_id = (
            f"typhur_sync_gold_probe_{probe_index + 1}_{description.key}"
        )

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor value."""
        probe = self._probe
        if probe is None:
            return None
        return self.entity_description.value_fn(probe)

    @property
    def options(self) -> list[str] | None:
        """Return enum options."""
        return self.entity_description.options
