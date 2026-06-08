"""Protocol parser and command builders for Typhur Sync Gold BLE."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from .const import DEFAULT_DEVICE_TYPE, DEVICE_TYPE_PROBE_COUNTS
from .models import BaseStationStatus, ProbeStatus


class TyphurParseError(ValueError):
    """Raised when a Typhur payload cannot be parsed."""


def normalize_address(address: str) -> str:
    """Normalize a Bluetooth address for stable storage."""
    return address.upper()


def address_to_device_id(address: str) -> str:
    """Return the protocol device ID from a Bluetooth address."""
    return normalize_address(address).replace(":", "")


def device_type_from_name(name: str | None) -> str | None:
    """Extract a non-authoritative device type hint from a Typhur BLE name."""
    if not name:
        return None
    for device_type in DEVICE_TYPE_PROBE_COUNTS:
        if f"-{device_type}-" in name or name.endswith(f"-{device_type}"):
            return device_type
    return None


def device_type_from_cmd_type(cmd_type: str | None) -> str | None:
    """Extract the device type from a command type string."""
    if not cmd_type or ":" not in cmd_type:
        return None
    prefix = cmd_type.split(":", 1)[0]
    if prefix in DEVICE_TYPE_PROBE_COUNTS:
        return prefix
    return None


def extract_device_type(payload: dict[str, Any]) -> str | None:
    """Extract a recognized device type from a response payload."""
    device_type = device_type_from_cmd_type(_optional_str(payload.get("cmdType")))
    if device_type is not None:
        return device_type
    device_type = _optional_str(payload.get("deviceType"))
    if device_type in DEVICE_TYPE_PROBE_COUNTS:
        return device_type
    return None


def extract_user_id(payload: dict[str, Any]) -> str | None:
    """Extract a user ID from a response payload."""
    user_id = payload.get("userId")
    if isinstance(user_id, str) and user_id:
        return user_id
    cmd_data = payload.get("cmdData")
    if isinstance(cmd_data, dict):
        user_id = cmd_data.get("userId")
        if isinstance(user_id, str) and user_id:
            return user_id
    return None


def _optional_int(value: Any) -> int | None:
    """Return value as int when possible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _optional_bool(value: Any) -> bool | None:
    """Return value as bool when possible."""
    if isinstance(value, bool):
        return value
    return None


def _optional_str_or_bool(value: Any) -> str | bool | None:
    """Return a string-like protocol status value without constraining enums."""
    if isinstance(value, str | bool):
        return value
    return None


def _optional_str(value: Any) -> str | None:
    """Return value as str when possible."""
    if isinstance(value, str):
        return value
    return None


def _int_tuple(value: Any) -> tuple[int | None, ...]:
    """Return a tuple of optional ints from a list-like value."""
    if not isinstance(value, list):
        return ()
    return tuple(_optional_int(item) for item in value)


def _dict_tuple(value: Any) -> tuple[dict[str, Any], ...]:
    """Return a tuple of dictionaries from a list-like value."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def parse_status_response(payload: dict[str, Any]) -> BaseStationStatus:
    """Parse a status report or status response payload."""
    if not isinstance(payload, dict):
        raise TyphurParseError("Response payload must be a dictionary")

    cmd_data = payload.get("cmdData")
    if not isinstance(cmd_data, dict):
        raise TyphurParseError("Response payload does not contain cmdData")

    device_type = extract_device_type(payload)

    probes_raw = cmd_data.get("probes")
    probes: list[ProbeStatus] = []
    if isinstance(probes_raw, list):
        for index, probe_raw in enumerate(probes_raw):
            if isinstance(probe_raw, dict):
                probes.append(parse_probe_status(index, probe_raw))

    return BaseStationStatus(
        device_type=device_type,
        probe_count=DEVICE_TYPE_PROBE_COUNTS.get(device_type, len(probes)),
        user_id=extract_user_id(payload),
        app_version=_optional_str(payload.get("appVersion")),
        control_board_version=_optional_str(payload.get("controlBoardVersion")),
        global_status=_optional_str(cmd_data.get("globalStatus")),
        battery_status=_optional_str(cmd_data.get("batteryStatus")),
        battery_value=_optional_int(cmd_data.get("batteryValue")),
        wifi_rssi=_optional_int(cmd_data.get("wifiRssi")),
        volume=_optional_str(cmd_data.get("volume")),
        connect_status=_optional_str(cmd_data.get("connectStatus")),
        has_wifi_config=_optional_bool(cmd_data.get("hasWifiConfig")),
        last_set_temperature=_optional_int(cmd_data.get("lastSetTemperature")),
        probes=tuple(probes),
        raw=payload,
    )


def parse_probe_status(index: int, payload: dict[str, Any]) -> ProbeStatus:
    """Parse a probe status payload."""
    return ProbeStatus(
        index=index,
        device_sn=_optional_str(payload.get("deviceSn")),
        probe_color=_optional_str(payload.get("probeColor")),
        cooking_state=_optional_str(payload.get("cookingState")),
        battery_value=_optional_int(payload.get("batteryValue")),
        engaged_status=_optional_str_or_bool(payload.get("engagedStatus")),
        cur_temperature=_optional_int(payload.get("curTemperature")),
        area_temperature=_int_tuple(payload.get("areaTemperature")),
        cur_ambient_temperature=_optional_int(payload.get("curAmbientTemperature")),
        temperature_percentile=_int_tuple(payload.get("temperaturePercentile")),
        cooking_mode=_optional_str(payload.get("cookingMode")),
        cook_uuid=_optional_str(payload.get("cookUuid")),
        start_client=_optional_str(payload.get("startClient")),
        set_params=_dict_tuple(payload.get("setParams")),
        total_cook_sec=_optional_int(payload.get("totalCookSec")),
        cur_cook_sec=_optional_int(payload.get("curCookSec")),
        cur_remained_sec=_optional_int(payload.get("curRemainedSec")),
        raw=payload,
    )


def build_command(
    address: str,
    device_type: str | None,
    cmd_type: str,
    cmd_seq_no: int,
    cmd_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Typhur JSON command payload."""
    now = time.time()
    cmd_id = uuid4().hex.lower()
    return {
        "cmdId": cmd_id,
        "cmdSeqNo": cmd_seq_no,
        "cmdType": cmd_type,
        "deviceId": address_to_device_id(address),
        "deviceType": device_type or DEFAULT_DEVICE_TYPE,
        "protocol": "BT",
        "serverTime": int(now * 1000),
        "serverTimeSecond": int(now),
        "cmdData": cmd_data or {},
    }


def build_auth_command(
    address: str, device_type: str | None, user_id: str, cmd_seq_no: int
) -> dict[str, Any]:
    """Build an authentication/trust request."""
    return build_command(
        address,
        device_type,
        "BT:apply:trust",
        cmd_seq_no,
        {
            "userId": user_id,
            "deviceModel": "TB132FU",
            "lengthUnit": "cm",
            "mode": "direct",
            "temperatureUnit": "C",
            "weightUnit": "g",
        },
    )


def build_status_command(
    address: str, device_type: str, cmd_seq_no: int
) -> dict[str, Any]:
    """Build a status request."""
    return build_command(
        address,
        device_type,
        f"{device_type}:status:request",
        cmd_seq_no,
    )


def build_cooking_data_command(
    address: str, device_type: str | None, cmd_seq_no: int
) -> dict[str, Any]:
    """Build a cooking data request."""
    return build_command(
        address,
        device_type,
        "BT:cooking:data:request",
        cmd_seq_no,
    )
