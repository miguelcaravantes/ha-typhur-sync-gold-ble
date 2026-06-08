# Typhur Sync Gold BLE — Home Assistant Custom Integration

[![GitHub Release](https://img.shields.io/github/v/release/miguelcaravantes/ha-typhur-sync-gold-ble)](https://github.com/miguelcaravantes/ha-typhur-sync-gold-ble/releases)
[![License](https://img.shields.io/github/license/miguelcaravantes/ha-typhur-sync-gold-ble)](LICENSE)

A Home Assistant custom integration for **Typhur Sync Gold** Bluetooth meat thermometers.

## Supported Devices

| Model | Device Type | Probes | Tested |
| :--- | :---: | :---: | :---: |
| Sync Gold Quad | WT04 | 4 | No |
| Sync Gold Dual | WT05 | 2 | Yes |

## Entities

### Base Station

| Entity | Description |
| :--- | :--- |
| Base Battery | Battery level (%) |
| Battery Status | Charging state: `normal`, `charged`, `charging` |
| Wi-Fi RSSI | Wi-Fi signal strength |
| Wi-Fi Configured | Whether Wi-Fi is set up |
| Connection Status | Current connection state |
| Volume | Speaker volume level |
| Last Set Temperature | Last target temperature set on the device |
| Online | Device is reachable via BLE |

### Per Probe

| Entity | Description |
| :--- | :--- |
| Core Temperature | Internal meat temperature |
| Ambient Temperature | Oven/smoker ambient temperature |
| Target Temperature | Set target temperature |
| Battery | Probe battery level (%) |
| Cooking State | Current cooking state |
| Cooking Mode | Active cooking mode |
| Engaged Status | Whether probe is inserted in meat |
| Current Cook Time | Elapsed cook time (seconds) |
| Remaining Cook Time | Estimated remaining time (seconds) |
| Area Temperature 1–5 | Zone temperatures around the probe |

## Requirements

- Home Assistant 2024.1+
- Bluetooth adapter or proxy within range of the base station

## Installation

### HACS (Recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/miguelcaravantes/ha-typhur-sync-gold-ble` as a **Integration**
3. Install, restart Home Assistant
4. Add integration via Settings → Devices & Services → Add Integration → **Typhur Sync Gold BLE**

### Manual

```bash
cd /config/custom_components
git clone https://github.com/miguelcaravantes/ha-typhur-sync-gold-ble.git typhur_sync_gold_ble
```

Restart Home Assistant, then add the integration via Settings → Devices & Services.

## Bluetooth Range

This integration communicates with the base station over **Bluetooth Low Energy (BLE)**. For reliable connectivity:

- **Keep the base station close** to your Home Assistant server or Bluetooth adapter (within ~10 meters / 30 feet, with minimal walls).
- If the base station is too far or behind walls/obstacles, use a **[Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/)** via ESPHome to extend BLE range.

### Setting up a Bluetooth Proxy

1. Flash an ESP32 device with [ESPHome](https://esphome.io)
2. Add the `bluetooth_proxy` component to your ESPHome config:

```yaml
bluetooth_proxy:
  active: true
```

3. Add the ESP32 to Home Assistant via the ESPHome integration
4. The proxy will automatically extend Bluetooth coverage to the base station

## How It Works

1. The integration discovers the base station via BLE advertising (name prefix `Typhur-*` or `TM-*`)
2. Connects and performs DH key exchange authentication with the device
3. Requests full status via encrypted BLE commands
4. Parses the status payload (zstd-compressed) and exposes all readings as HA entities
5. Refreshes data every 5 minutes

## Troubleshooting

| Issue | Solution |
| :--- | :--- |
| Device not discovered | Ensure Bluetooth is enabled and the base station is powered on and in pairing mode |
| Entities unavailable | Check BLE signal strength; move base station closer or add a Bluetooth Proxy |
| Authentication fails | Remove and re-add the integration to trigger re-authentication |

## Acknowledgements

Special thanks to [@zuyan9](https://github.com/zuyan9) for his work on Typhur device integrations — while targeting different models from the same manufacturer, his implementations were a valuable reference for understanding how to connect to the API.

## License

MIT
