"""Binary sensor entities for Typhur Sync Gold BLE."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TyphurDataUpdateCoordinator
from .entity import TyphurEntity
from .models import BaseStationStatus


@dataclass(frozen=True, kw_only=True)
class TyphurBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a base binary sensor."""

    value_fn: Callable[[BaseStationStatus], bool | None]


BASE_BINARY_SENSOR_DESCRIPTIONS: tuple[TyphurBinarySensorEntityDescription, ...] = (
    TyphurBinarySensorEntityDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda status: status.global_status == "online"
        if status.global_status is not None
        else None,
    ),
    TyphurBinarySensorEntityDescription(
        key="wifi_configured",
        translation_key="wifi_configured",
        value_fn=lambda status: status.has_wifi_config,
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Typhur binary sensors."""
    coordinator: TyphurDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        TyphurBaseBinarySensor(coordinator, description)
        for description in BASE_BINARY_SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class TyphurBaseBinarySensor(TyphurEntity, BinarySensorEntity):
    """Base station binary sensor."""

    entity_description: TyphurBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TyphurDataUpdateCoordinator,
        description: TyphurBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_suggested_object_id = f"typhur_sync_gold_base_station_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
