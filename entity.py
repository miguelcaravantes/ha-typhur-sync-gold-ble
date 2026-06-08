"""Base entities for Typhur Sync Gold BLE."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_MODEL_NAMES, DOMAIN, MANUFACTURER
from .coordinator import TyphurDataUpdateCoordinator


class TyphurEntity(CoordinatorEntity[TyphurDataUpdateCoordinator]):
    """Base entity for Typhur entities."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Return if entity data is available."""
        return super().available and self.coordinator.data is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        data = self.coordinator.data
        device_type = self.coordinator.device_type
        model = DEVICE_TYPE_MODEL_NAMES.get(device_type or "", device_type)
        if data and data.model_name:
            model = data.model_name
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(dr.CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer=MANUFACTURER,
            model=model,
            name="Typhur Sync Gold Base Station",
        )


class TyphurProbeEntity(TyphurEntity):
    """Base entity for one Typhur probe slot."""

    def __init__(self, coordinator: TyphurDataUpdateCoordinator, probe_index: int) -> None:
        """Initialize a probe entity."""
        super().__init__(coordinator)
        self.probe_index = probe_index

    @property
    def device_info(self) -> DeviceInfo:
        """Return probe device registry information."""
        probe = self._probe
        return DeviceInfo(
            identifiers={(
                DOMAIN,
                f"{self.coordinator.address}_probe_{self.probe_index + 1}",
            )},
            manufacturer=MANUFACTURER,
            model="Sync Gold Probe",
            name=f"Typhur Sync Gold Probe {self.probe_index + 1}",
            serial_number=probe.device_sn if probe else None,
            sw_version=self.coordinator.data.app_version if self.coordinator.data else None,
            via_device=(DOMAIN, self.coordinator.address),
        )

    @property
    def _probe(self):
        """Return the current probe status."""
        data = self.coordinator.data
        if data is None or self.probe_index >= len(data.probes):
            return None
        return data.probes[self.probe_index]
