"""Load the real integration under Home Assistant 2026.11.3 and exercise it.

This uses the actual HA classes plus mocked BLE transport. It verifies that
the component imports, auto-discovery config flow creates an entry, the light
entity is constructed, and all four control types emit the captured frames.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import pathlib
import sys
import time
from unittest.mock import AsyncMock, MagicMock


def mock_hass():
    hass = MagicMock()

    def create_task(coro):
        coro.close()
        return MagicMock()

    hass.async_create_task = create_task
    return hass

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth.match import ble_device_matches
from homeassistant.exceptions import HomeAssistantError
from habluetooth.models import BluetoothServiceInfoBleak

CONFIG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONFIG))

integ = importlib.import_module("custom_components.govee_h6010_ble")
light_mod = importlib.import_module("custom_components.govee_h6010_ble.light")
flow_mod = importlib.import_module("custom_components.govee_h6010_ble.config_flow")
protocol = importlib.import_module("custom_components.govee_h6010_ble.protocol")


class FakeChar:
    def __init__(self):
        from custom_components.govee_h6010_ble.const import NOTIFY_UUID
        self.uuid = NOTIFY_UUID
        self.properties = ["notify"]
        self.handle = 0x000E


class FakeService:
    def __init__(self):
        self.characteristics = [FakeChar()]


class FakeBleakClient:
    frames: list[bytes] = []
    connections: int = 0

    def __init__(self, device, timeout=None):
        self.device = device
        self.services = [FakeService()]
        self._connected = False

    @property
    def is_connected(self):
        return self._connected

    async def connect(self):
        type(self).connections += 1
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def start_notify(self, char, callback):
        self._notify_callback = callback

    async def stop_notify(self, char):
        self._notify_callback = None

    async def write_gatt_char(self, char, frame, response=False):
        frame = bytes(frame)
        self.frames.append(frame)
        if frame[0] != 0xAA or not hasattr(self, "_notify_callback"):
            return
        replies = {
            0x01: bytes.fromhex("aa010100000000000000000000000000000000aa"),
            0x04: bytes.fromhex(
                protocol.frame(0x04, bytes([50]), read=True).hex()
            ),
            0x05: bytes.fromhex(
                protocol.frame(
                    0x05, bytes([0x0D, 255, 0, 0, 0, 0]), read=True
                ).hex()
            ),
        }
        reply = replies.get(frame[1])
        if reply:
            self._notify_callback(char, bytearray(reply))


def make_service_info(name: str, address: str) -> BluetoothServiceInfoBleak:
    """Build the same object HA passes to async_step_bluetooth."""
    device = BLEDevice(address, name, None)
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        rssi=-60,
        tx_power=None,
        platform_data=None,
    )
    return BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        device, adv, "local", time.time(), True
    )


def test_manifest_matchers() -> None:
    manifest = json.loads(
        (CONFIG / "custom_components/govee_h6010_ble/manifest.json").read_text()
    )
    matchers = manifest["bluetooth"]
    for name in ("ihoment_H6010_XXXX", "ihoment_H6008_XXXX"):
        info = make_service_info(name, "AA:BB:CC:DD:EE:FF")
        matched = [m for m in matchers if ble_device_matches(m, info)]
        assert matched, f"no manifest matcher for {name}"
        print("MANIFEST MATCH:", name, "->", matched[0]["local_name"])


async def test_config_flow() -> dict:
    from homeassistant.config_entries import HANDLERS

    handler = HANDLERS.get("govee_h6010_ble")
    assert handler is flow_mod.GoveeH6010BLEConfigFlow, handler
    print("CONFIG FLOW HANDLER REGISTERED:", handler.__name__)

    flow = flow_mod.GoveeH6010BLEConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create_entry", **kw}

    discovery = make_service_info("ihoment_H6010_XXXX", "AA:BB:CC:DD:EE:FF")
    first = await flow.async_step_bluetooth(discovery)
    assert first["type"] == "form" and first["step_id"] == "bluetooth_confirm", first
    entry = await flow.async_step_bluetooth_confirm({})
    assert entry["type"] == "create_entry", entry
    assert entry["data"]["address"] == "AA:BB:CC:DD:EE:FF", entry
    assert entry["title"] == "ihoment_H6010_XXXX", entry

    flow2 = flow_mod.GoveeH6010BLEConfigFlow()
    flow2.hass = MagicMock()
    flow2.context = {}
    flow2.async_set_unique_id = AsyncMock()
    flow2._abort_if_unique_id_configured = MagicMock()
    flow2.async_show_form = lambda **kw: {"type": "form", **kw}
    flow2.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
    discovery2 = make_service_info("ihoment_H6008_XXXX", "11:22:33:44:55:66")
    await flow2.async_step_bluetooth(discovery2)
    entry2 = await flow2.async_step_bluetooth_confirm({})
    assert entry2["data"]["model"] == "H6008", entry2
    print("H6008 DISCOVERY:", entry2)
    return entry


async def test_light() -> list[str]:
    hass = mock_hass()
    light_mod.async_ble_device_from_address = lambda *args, **kwargs: object()
    light_mod.BleakClient = FakeBleakClient
    FakeBleakClient.frames = []
    FakeBleakClient.connections = 0

    entity = light_mod.GoveeH6010Light(
        hass, "AA:BB:CC:DD:EE:FF", "ihoment_H6008_XXXX", "entry1", "H6008"
    )
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on(brightness=186, rgb_color=(255, 0, 0))
    await entity.async_turn_on(color_temp_kelvin=5900)
    entity._last_command = 0.0  # simulate a poll well after the last command
    await entity.async_update()
    assert entity.is_on is True, entity.is_on
    assert entity.brightness == 128, entity.brightness
    assert entity.rgb_color == (255, 0, 0), entity.rgb_color
    print("POLL RESULT:", entity.is_on, entity.brightness, entity.rgb_color)
    await entity.async_turn_off()

    # A stale status reply must not undo a just-sent colour.
    entity._state_replies = {
        0x01: bytes.fromhex("aa010100000000000000000000000000000000aa"),
        0x04: bytes.fromhex(protocol.frame(0x04, bytes([10]), read=True).hex()),
        0x05: bytes.fromhex("aa050dffb16d0bb8000000000000000000000032"),
    }
    entity._apply_status_replies()
    assert entity.rgb_color == (255, 0, 0), entity.rgb_color
    assert entity.brightness == 128, entity.brightness
    assert entity.is_on is False, entity.is_on  # power-off reply also ignored
    print("STALE POLL IGNORED:", entity.rgb_color, entity.brightness)

    assert entity.unique_id == "AA:BB:CC:DD:EE:FF_light"
    assert entity.device_info["model"] == "H6008"
    assert FakeBleakClient.connections == 1, FakeBleakClient.connections
    print("BLE CONNECTIONS FOR ALL COMMANDS:", FakeBleakClient.connections)
    all_frames = [bytes(frame) for frame in FakeBleakClient.frames]
    queries = [frame for frame in all_frames if frame[0] == 0xAA]
    assert len(queries) == 3, queries
    print("STATUS QUERIES:", len(queries))
    frames = [frame.hex() for frame in all_frames if frame[0] == 0x33]
    assert frames == [
        protocol.power(True).hex(),
        protocol.brightness(73).hex(),
        protocol.rgb(255, 0, 0).hex(),
        protocol.color_temperature(
            5900, protocol.kelvin_to_rgb(5900)
        ).hex(),
        protocol.power(False).hex(),
    ], frames
    return frames


async def test_failed_connect_keeps_state() -> None:
    entity = light_mod.GoveeH6010Light(
        mock_hass(), "11:22:33:44:55:66", "H6008 test", "entry2", "H6008"
    )
    entity.async_write_ha_state = lambda: None
    entity._async_client = AsyncMock(side_effect=HomeAssistantError("link busy"))
    await entity.async_turn_on(rgb_color=(0, 255, 0))
    assert entity.rgb_color == (0, 255, 0), entity.rgb_color
    print("FAILED CONNECT KEEPS STATE:", entity.rgb_color)


async def test_fresh_power_on_ignores_stale_off_reply() -> None:
    light_mod.async_ble_device_from_address = lambda *args, **kwargs: object()
    light_mod.BleakClient = FakeBleakClient
    entity = light_mod.GoveeH6010Light(
        mock_hass(), "11:22:33:44:55:66", "H6008 power", "entry3", "H6008"
    )
    entity.async_write_ha_state = lambda: None
    await entity.async_turn_on()
    assert entity.is_on is True, entity.is_on
    entity._state_replies = {
        0x01: bytes.fromhex("aa010000000000000000000000000000000000ab"),
    }
    entity._apply_status_replies()
    assert entity.is_on is True, entity.is_on
    print("FRESH POWER ON KEPT:", entity.is_on)


async def main() -> None:
    test_manifest_matchers()
    await test_failed_connect_keeps_state()
    await test_fresh_power_on_ignores_stale_off_reply()
    entry = await test_config_flow()
    print("AUTODISCOVERY:", entry)
    frames = await test_light()
    print("FRAMES:")
    for frame in frames:
        print(" ", frame)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    with contextlib.suppress(RuntimeError):
        asyncio.run(main())
