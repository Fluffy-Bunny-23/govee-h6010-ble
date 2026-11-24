# Govee H6010 BLE for Home Assistant

A small, local-only Home Assistant integration for the Govee H6010 smart LED.
It exposes one light entity with on/off, brightness, RGB color and color
temperature. No Govee account, no cloud, no LAN API, no MQTT bridge.

The light is discovered automatically from its BLE advertisement
(`ihoment_H6010_*`), so there is nothing to pair and no code to enter. It works
with a local Bluetooth adapter and with ESPHome Bluetooth proxies such as the
Everything Presence One, because it obtains the BLE device from Home Assistant's
bluetooth integration rather than opening a raw HCI socket.

## Install

1. Add this repository to HACS as a custom repository of type **Integration**.
2. Install **Govee H6010 BLE** and restart Home Assistant.
3. The light appears under Settings > Devices & Services as a discovered
   device. Confirm it, and the `Light` entity is ready.

Manual install: copy `custom_components/govee_h6010_ble` into your Home
Assistant `config/custom_components` directory and restart.

## Requirements

- Home Assistant with the Bluetooth integration enabled.
- A Bluetooth adapter in range of the light, or an ESPHome Bluetooth proxy.
  An Everything Presence One with `esp32_ble_tracker` and `bluetooth_proxy`
  works.
- Bluetooth must be able to make *connectable* connections. Passive-only
  scanners will see the advertisement but cannot control the light.

## Why it looks the way it does

With Wi-Fi available, the Govee app controls this light through its cloud over
TLS. Only when the network is unavailable does it fall back to BLE. This
integration skips the cloud entirely and speaks the 20-byte Govee BLE frame
format directly:

```
byte 0      command prefix (0x33 write, 0xAA query)
byte 1      command: 0x01 power, 0x04 brightness, 0x05 color
bytes 2-18  payload, zero padded
byte 19     XOR of bytes 0-18
```

Frames were captured from a real H6010 with Android HCI snoop logging and are
verified byte-for-byte in `tests/test_protocol.py`. Control is
write-without-response to `00010203-0405-0607-0809-0a0b0c0d2b11`.

Brightness is a percentage (1-100) on this light. Color uses sub-mode `0x0D`
with an RGB triplet and Kelvin fields. Kelvin range is 2700-6500.

State is optimistic. The integration does not poll the device, which keeps it
friendly to Bluetooth proxies and to battery-powered scanners.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q custom_components tests
python3 -m json.tool custom_components/govee_h6010_ble/manifest.json >/dev/null
```

No Home Assistant installation is needed to run the protocol tests.
