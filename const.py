"""Constants for the Typhur Sync Gold BLE integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "typhur_sync_gold_ble"
NAME = "Typhur Sync Gold BLE"
MANUFACTURER = "Typhur"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

CONF_DEVICE_TYPE = "device_type"
CONF_PROBE_COUNT = "probe_count"
CONF_USER_ID = "user_id"

DEVICE_NAME_PREFIXES = ("Typhur-",)
DEFAULT_DEVICE_TYPE = "WT05"
DUMMY_USER_ID = "000000000000000000"

WRITE_CHARACTERISTIC_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

DEVICE_TYPE_PROBE_COUNTS = {
    "WT04": 4,
    "WT05": 2,
}

DEVICE_TYPE_MODEL_NAMES = {
    "WT04": "Sync Gold Quad (WT04)",
    "WT05": "Sync Gold Dual (WT05)",
}

DEFAULT_SCAN_DURATION = 10
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)
DEFAULT_COMMAND_TIMEOUT = 10
DEFAULT_BLE_MTU_PAYLOAD = 180

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

DH_PRIME_HEX = (
    "00c7391ab1a6575775fba187f58ccfe9eaee0f41ab1e9ef57be14bd4b5e28a9"
    "be1c54e0b0cf7bc66b3bcfbbd7ab013a7a92fb47dc6a0ca97cb4bfbf4b7c3"
    "d2f9b2d87e1451f28b3e839e55a73e5bf02bfa40411ed0262fc7df7b0b"
    "694901f4c71e9a2f6412170ba37af9391ab0b12bcbe4ef43d1a49a941cd99"
    "e2a8626e2ebaf23"
)
DH_PRIME = int(DH_PRIME_HEX, 16)
DH_GENERATOR = 5
