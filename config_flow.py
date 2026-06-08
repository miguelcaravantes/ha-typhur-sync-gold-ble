"""Config flow for Typhur Sync Gold BLE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth, onboarding
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_DEVICE_TYPE, DEVICE_NAME_PREFIXES, DOMAIN
from .parser import device_type_from_name, normalize_address


@dataclass(slots=True)
class DiscoveredDevice:
    """A discovered Typhur BLE device."""

    address: str
    name: str
    device_type: str | None


class TyphurSyncGoldBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Typhur Sync Gold BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery: DiscoveredDevice | None = None
        self._discovered_devices: dict[str, DiscoveredDevice] = {}

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle Bluetooth discovery."""
        name = discovery_info.name or discovery_info.address
        if not name.startswith(DEVICE_NAME_PREFIXES):
            return self.async_abort(reason="not_supported")

        address = normalize_address(discovery_info.address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovery = DiscoveredDevice(
            address=address,
            name=name,
            device_type=device_type_from_name(name),
        )
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm Bluetooth discovery."""
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            if self._discovery is None:
                return self.async_abort(reason="unknown")
            return self._async_create_entry(self._discovery)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual setup by selecting a discovered Typhur device."""
        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            discovery = self._discovered_devices[address]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._async_create_entry(discovery)

        await bluetooth.async_request_active_scan(self.hass)
        current_ids = self._async_current_ids()
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            name = service_info.name or service_info.address
            address = normalize_address(service_info.address)
            if (
                not name.startswith(DEVICE_NAME_PREFIXES)
                or address in current_ids
                or address in self._discovered_devices
            ):
                continue
            self._discovered_devices[address] = DiscoveredDevice(
                address=address,
                name=name,
                device_type=device_type_from_name(name),
            )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        options = {
            address: f"{device.name} ({address})"
            for address, device in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(options)}),
        )

    def _async_create_entry(self, discovery: DiscoveredDevice) -> FlowResult:
        """Create a config entry from a discovered device."""
        data: dict[str, str] = {
            CONF_ADDRESS: discovery.address,
            CONF_NAME: discovery.name,
        }
        if discovery.device_type is not None:
            data[CONF_DEVICE_TYPE] = discovery.device_type

        return self.async_create_entry(title=discovery.name, data=data)
