"""Coordinator for Typhur Sync Gold BLE devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    TyphurBleClient,
    TyphurConnectionError,
    TyphurError,
)
from .const import (
    CONF_DEVICE_TYPE,
    CONF_PROBE_COUNT,
    CONF_USER_ID,
    DEFAULT_DEVICE_TYPE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .models import BaseStationStatus
from .parser import device_type_from_name, normalize_address

_LOGGER = logging.getLogger(__name__)


class TyphurDataUpdateCoordinator(DataUpdateCoordinator[BaseStationStatus | None]):
    """Coordinate one Typhur Sync Gold BLE device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.address = normalize_address(entry.data[CONF_ADDRESS])
        self.name = entry.data.get(CONF_NAME, self.address)
        self.device_type: str | None = entry.data.get(CONF_DEVICE_TYPE)
        self.user_id: str | None = entry.data.get(CONF_USER_ID)
        self.probe_count = int(entry.data.get(CONF_PROBE_COUNT, 0) or 0)
        self.client = TyphurBleClient(
            hass,
            self.address,
            self.name,
            status_callback=self._async_status_received,
            unavailable_callback=self._async_device_unavailable,
        )
        self._unavailable_cancel: Callable[[], None] | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.address}",
            config_entry=entry,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_setup(self) -> None:
        """Set up Bluetooth availability tracking."""
        if bluetooth.async_scanner_count(self.hass, connectable=True) <= 0:
            raise ConfigEntryNotReady("No connectable Bluetooth adapter is available")
        self._unavailable_cancel = bluetooth.async_track_unavailable(
            self.hass, self._async_unavailable_callback, self.address, connectable=True
        )

    async def _async_update_data(self) -> BaseStationStatus | None:
        """Fetch the latest data from the BLE device."""
        try:
            await self.client.connect()
            
            # Authenticate and capture any discovered user_id
            auth_result = await self.client.authenticate(self.user_id, self.device_type)
            
            # If we discovered a new user_id, save it and retry auth
            if auth_result.user_id and auth_result.user_id != self.user_id:
                _LOGGER.warning(
                    "Typhur %s: discovered user ID %s, saving and retrying auth",
                    self.address,
                    auth_result.user_id,
                )
                self.user_id = auth_result.user_id
                self._async_update_entry_data({CONF_USER_ID: self.user_id})
                # Wait before retrying authentication
                await asyncio.sleep(0.5)
                # Retry authentication with the correct user_id
                auth_result = await self.client.authenticate(self.user_id, self.device_type)

            if auth_result.device_type and auth_result.device_type != self.device_type:
                self.device_type = auth_result.device_type
                self._async_update_entry_data({CONF_DEVICE_TYPE: self.device_type})

            discovered_type = self.device_type or device_type_from_name(self.name) or DEFAULT_DEVICE_TYPE

            # Wait before requesting status
            await asyncio.sleep(0.5)
            status = await self.client.request_status(discovered_type)
            self._async_apply_status(status)
            return status
        except (TyphurConnectionError, TyphurError) as err:
            raise UpdateFailed(f"Error communicating with Typhur device: {err}") from err

    async def async_shutdown(self) -> None:
        """Shut down coordinator resources."""
        if self._unavailable_cancel is not None:
            self._unavailable_cancel()
            self._unavailable_cancel = None
        await self.client.disconnect()

    @callback
    def _async_status_received(self, status: BaseStationStatus) -> None:
        """Handle a pushed status update."""
        self._async_apply_status(status)
        self.async_set_updated_data(status)

    @callback
    def _async_apply_status(self, status: BaseStationStatus) -> None:
        """Apply discovered status metadata to the config entry."""
        updates: dict[str, str | int] = {}
        if status.device_type and status.device_type != self.device_type:
            self.device_type = status.device_type
            updates[CONF_DEVICE_TYPE] = status.device_type
        probe_count = status.discovered_probe_count
        if probe_count and probe_count != self.probe_count:
            self.probe_count = probe_count
            updates[CONF_PROBE_COUNT] = probe_count
        if status.user_id and status.user_id != self.user_id:
            self.user_id = status.user_id
            updates[CONF_USER_ID] = status.user_id
        if updates:
            self._async_update_entry_data(updates)

    @callback
    def _async_update_entry_data(self, updates: dict[str, str | int]) -> None:
        """Persist config entry data updates."""
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, **updates},
        )

    @callback
    def _async_device_unavailable(self) -> None:
        """Handle client-level unavailable notifications."""
        self.async_set_updated_data(None)

    @callback
    def _async_unavailable_callback(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle Home Assistant Bluetooth unavailability tracking."""
        self._async_device_unavailable()
