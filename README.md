# Govee H6010 BLE for Home Assistant

A small, local-only Home Assistant integration for Govee H6010 and H6008
lights. It exposes one light entity with on/off, brightness, RGB color and
color temperature. No Govee account, no cloud, no LAN API, no MQTT bridge.

Lights are discovered automatically from their BLE advertisements
(`ihoment_H6010_*` and `ihoment_H6008_*`), so there is nothing to pair and no
code to enter. It works
with a local Bluetooth adapter and with ESPHome Bluetooth proxies such as the
Everything Presence One, because it obtains the BLE device from Home Assistant's
bluetooth integration rather than opening a raw HCI socket.

## Install with HACS

Add this repository URL in HACS as a custom repository of type **Integration**:

```
https://github.com/Fluffy-Bunny-23/govee-h6010-ble
```

Steps: HACS → ⋮ → Custom repositories → Repository: paste the URL above,
Category: Integration → Add. Then search HACS for **Govee H6010 BLE**, install
it, and restart Home Assistant.

The light is then discovered automatically over Bluetooth. Confirm it under
Settings > Devices & Services and the `Light` entity is ready.

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

The UI state is updated before the radio write and the service call returns
immediately; BLE writes run in a background task, so sliders and toggles do not
wait on a proxy round-trip. A failed write is retried once on the same link
before the connection is rebuilt. One BLE connection is kept open and reused for all commands and
status reads; opening a connection per service call is what otherwise makes
proxy control feel slow. If that connection drops, it is rebuilt from the best
available adapter or proxy.

A lightweight power query is sent every ten seconds over the open connection,
matching what the Govee app does to keep idle links alive. If that link is
taken by another controller, the entity drops its reference without waiting
for bleak's long disconnect timeout.

Every five minutes the entity asks the light for its real state over the same
connection, so changes made from the Govee app or another controller are
picked up. When the light is off it only sends the one-byte power query; when
on it queries power, brightness and color. Status replies arrive on the
notify characteristic (`aa 01` power, `aa 04` brightness, `aa 05 0d` color).
If notifications are unavailable through a proxy, the entity simply keeps its
optimistic state. A failed command no longer rolls the UI state back: the
requested value stays visible until the next successful status poll, so the
colour no longer flashes back after another controller changes the light.

For 60 seconds after a command, polling ignores brightness and colour replies.
Some H6008 firmware reports an old Kelvin field after an RGB command, which
made the colour picker snap back immediately even though the light was correct.
Power is held for the same window, so a stale off reply cannot make a light you
just switched on flip back off. External changes made in that minute are
picked up at the next poll afterwards.

Both models use the same 20-byte command dialect. H6008 was verified from a
captured session with two lights (desk and floor lamp, firmware 1.00.69): the
app sends `33 04 <percent>` for brightness, `33 05 0d <R G B>` for color and
`33 05 0d <tint> <Kelvin hi/lo> <tint>` for color temperature, the same frames
the H6010 integration emits.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q custom_components tests
python3 -m json.tool custom_components/govee_h6010_ble/manifest.json >/dev/null
```

No Home Assistant installation is needed for the protocol tests.

`tests/ha_runtime_check.py` loads the real component under an installed Home
Assistant (verified with 2026.11.3) and checks: the manifest Bluetooth matchers
against real `BluetoothServiceInfoBleak` objects for both `ihoment_H6010_*` and
`ihoment_H6008_*`, config-flow auto-discovery producing an entry with the
correct model for each, and the light entity emitting the expected frames for
power, brightness, RGB and color temperature. BLE transport is mocked there,
so it does not prove a real radio or proxy connection.

```sh
python3 -m pip install homeassistant aiousbwatcher aiohasupervisor serialx habluetooth
python3 tests/ha_runtime_check.py
```

Known limitation: an install has not been exercised against a live Home
Assistant instance with a physical Bluetooth proxy. The BLE path uses Home
Assistant's `async_ble_device_from_address`, which is the standard API for
connectable remote adapters, but on-site verification is still worth doing.
