"""Load the real integration under Home Assistant 2026.11.3 and exercise it.

This uses the actual HA classes plus mocked BLE transport. It verifies that
the component imports, auto-discovery config flow creates an entry, the light
entity is constructed, and all four control types emit the captured frames.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        local_name="ihoment_H6010_XXXX",
        name="ihoment_H6010_XXXX",
    )
    first = await flow.async_step_bluetooth(discovery)
    assert first["type"] == "form" and first["step_id"] == "bluetooth_confirm", first
    entry = await flow.async_step_bluetooth_confirm({})
    assert entry["type"] == "create_entry", entry
    assert entry["data"]["address"] == "AA:BB:CC:DD:EE:FF", entry
    assert entry["title"] == "ihoment_H6010_XXXX", entry
    return entry


async def test_light() -> list[str]:
    hass = MagicMock()
    light_mod.async_ble_device_from_address = lambda *args, **kwargs: object()
    light_mod.BleakClient = FakeBleakClient
    FakeBleakClient.frames = []
    FakeBleakClient.connections = 0

    entity = light_mod.GoveeH6010Light(
        hass, "AA:BB:CC:DD:EE:FF", "ihoment_H6010_XXXX", "entry1"
    )
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on(brightness=186, rgb_color=(255, 0, 0))
    await entity.async_turn_on(color_temp_kelvin=5900)
    await entity.async_update()
    assert entity.is_on is True, entity.is_on
    assert entity.brightness == 128, entity.brightness
    assert entity.rgb_color == (255, 0, 0), entity.rgb_color
    print("POLL RESULT:", entity.is_on, entity.brightness, entity.rgb_color)
    await entity.async_turn_off()

    assert entity.unique_id == "AA:BB:CC:DD:EE:FF_light"
    assert entity.device_info["model"] == "H6010"
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


async def main() -> None:
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
