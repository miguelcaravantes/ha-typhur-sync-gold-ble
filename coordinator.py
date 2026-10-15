"""Coordinator for Typhur Sync Gold BLE devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import time

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
    DEVICE_TYPE_PROBE_COUNTS,
    DOMAIN,
)
from .models import BaseStationStatus
from .parser import device_type_from_name, normalize_address

_LOGGER = logging.getLogger(__name__)
ADVERTISEMENT_RECONNECT_COOLDOWN = 15.0


class TyphurDataUpdateCoordinator(DataUpdateCoordinator[BaseStationStatus | None]):
    """Coordinate one Typhur Sync Gold BLE device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.address = normalize_address(entry.data[CONF_ADDRESS])
        self.name = entry.data.get(CONF_NAME, self.address)
        self.device_type: str | None = entry.data.get(
            CONF_DEVICE_TYPE
        ) or device_type_from_name(self.name)
        self.user_id: str | None = entry.data.get(CONF_USER_ID)
        self.probe_count = int(
            entry.data.get(CONF_PROBE_COUNT, 0)
            or DEVICE_TYPE_PROBE_COUNTS.get(self.device_type or "", 0)
            or 0
        )
        self.client = TyphurBleClient(
            hass,
            self.address,
            self.name,
            status_callback=self._async_status_received,
            unavailable_callback=self._async_device_unavailable,
        )
        self._advertisement_cancel: Callable[[], None] | None = None
        self._unavailable_cancel: Callable[[], None] | None = None
        self._unavailable_refresh_task: asyncio.Task[None] | None = None
        self._last_connect_attempt = 0.0
        self._disconnected_at = 0.0
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.address}",
            config_entry=entry,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_setup(self) -> None:
        """Set up Bluetooth availability and advertisement tracking."""
        self._last_connect_attempt = time.monotonic()
        self._unavailable_cancel = bluetooth.async_track_unavailable(
            self.hass, self._async_unavailable_callback, self.address, connectable=True
        )
        self._advertisement_cancel = bluetooth.async_register_callback(
            self.hass,
            self._async_bluetooth_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    def _is_ble_device_present(self) -> bool:
        """Return true if Home Assistant has a fresh connectable advertisement."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        if service_info is None:
            return False
        if self._disconnected_at and service_info.time < self._disconnected_at:
            return False
        return (
            bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            is not None
        )

    async def _async_update_data(self) -> BaseStationStatus | None:
        """Fetch the latest data from the BLE device."""
        was_connected = self.client.is_connected
        if not was_connected and not self._is_ble_device_present():
            _LOGGER.debug(
                "Typhur %s: device is not available via Bluetooth (likely powered off)",
                self.address,
            )
            return None

        attempt_started_at = time.monotonic()
        self._last_connect_attempt = attempt_started_at
        try:
            return await self._async_fetch_status()
        except (TyphurConnectionError, TyphurError) as first_err:
            await self.client.disconnect()
            if not was_connected:
                self._disconnected_at = attempt_started_at
                _LOGGER.debug(
                    "Typhur %s: device is unreachable (likely powered off): %s",
                    self.address,
                    first_err,
                )
                return None

            if not self._is_ble_device_present():
                self._disconnected_at = attempt_started_at
                _LOGGER.debug(
                    "Typhur %s: device is offline (likely powered off): %s",
                    self.address,
                    first_err,
                )
                return None

            _LOGGER.debug(
                "Typhur %s: update failed on existing session, resetting and retrying once: %s",
                self.address,
                first_err,
            )
            await asyncio.sleep(1.0)
            retry_started_at = time.monotonic()
            self._last_connect_attempt = retry_started_at
            try:
                return await self._async_fetch_status()
            except (TyphurConnectionError, TyphurError) as err:
                await self.client.disconnect()
                self._disconnected_at = retry_started_at
                _LOGGER.debug(
                    "Typhur %s: device is unreachable (likely powered off): %s",
                    self.address,
                    err,
                )
                return None

    async def _async_fetch_status(self) -> BaseStationStatus:
        """Fetch the latest status from a fresh or existing BLE session."""
        await self.client.connect()

        auth_result = await self.client.authenticate(self.user_id, self.device_type)

        if auth_result.user_id and auth_result.user_id != self.user_id:
            _LOGGER.debug(
                "Typhur %s: discovered and saved a user ID; retrying authentication",
                self.address,
            )
            self.user_id = auth_result.user_id
            self._async_update_entry_data({CONF_USER_ID: self.user_id})
            await asyncio.sleep(0.5)
            auth_result = await self.client.authenticate(self.user_id, self.device_type)

        if auth_result.device_type and auth_result.device_type != self.device_type:
            self.device_type = auth_result.device_type
            self._async_update_entry_data({CONF_DEVICE_TYPE: self.device_type})

        discovered_type = self.device_type or device_type_from_name(self.name) or DEFAULT_DEVICE_TYPE

        await asyncio.sleep(0.5)
        status = await self.client.request_status(discovered_type)
        self._async_apply_status(status)
        return status

    async def async_shutdown(self) -> None:
        """Shut down coordinator resources."""
        if self._unavailable_refresh_task is not None:
            self._unavailable_refresh_task.cancel()
            self._unavailable_refresh_task = None
        if self._advertisement_cancel is not None:
            self._advertisement_cancel()
            self._advertisement_cancel = None
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
        self._disconnected_at = time.monotonic()
        if (
            self._unavailable_refresh_task is not None
            and not self._unavailable_refresh_task.done()
        ):
            return
        _LOGGER.debug(
            "Typhur %s: BLE connection became unavailable; checking for advertisements",
            self.address,
        )
        self._unavailable_refresh_task = self.hass.async_create_task(
            self._async_refresh_after_unavailable(delay=3.0)
        )

    async def _async_refresh_after_unavailable(self, delay: float = 1.0) -> None:
        """Request a coordinator refresh after a BLE unavailable or advertisement event."""
        try:
            await asyncio.sleep(delay)
            await self.async_request_refresh()
        finally:
            self._unavailable_refresh_task = None

    @callback
    def _async_unavailable_callback(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle Home Assistant Bluetooth unavailability tracking."""
        self._disconnected_at = time.monotonic()
        _LOGGER.debug("Typhur %s: Bluetooth device marked unavailable", self.address)
        if self.data is not None:
            self.async_set_updated_data(None)

    @callback
    def _async_bluetooth_advertisement(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Reconnect when a powered-off device starts advertising again."""
        self._disconnected_at = 0.0
        if self.client.is_connected:
            return
        if (
            self._unavailable_refresh_task is not None
            and not self._unavailable_refresh_task.done()
        ):
            return
        if time.monotonic() - self._last_connect_attempt < ADVERTISEMENT_RECONNECT_COOLDOWN:
            return
        _LOGGER.debug(
            "Typhur %s: BLE advertisement received while disconnected; scheduling refresh",
            self.address,
        )
        self._last_connect_attempt = time.monotonic()
        self._unavailable_refresh_task = self.hass.async_create_task(
            self._async_refresh_after_unavailable(delay=1.0)
        )
