"""Protocol tests, using frames captured from a real H6010 over BLE."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "govee_h6010_ble"
    / "protocol.py"
)
spec = importlib.util.spec_from_file_location("govee_protocol", PROTOCOL_PATH)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)


class ProtocolTest(unittest.TestCase):
    def test_power_frames_match_capture(self) -> None:
        self.assertEqual(
            protocol.power(True).hex(),
            "3301010000000000000000000000000000000033",
        )
        self.assertEqual(
            protocol.power(False).hex(),
            "3301000000000000000000000000000000000032",
        )

    def test_brightness_frames_match_capture(self) -> None:
        self.assertEqual(
            protocol.brightness(19).hex(),
            "3304130000000000000000000000000000000024",
        )
        self.assertEqual(
            protocol.brightness(73).hex(),
            "330449000000000000000000000000000000007e",
        )

    def test_rgb_frames_match_capture(self) -> None:
        self.assertEqual(
            protocol.rgb(255, 0, 0).hex(),
            "33050dff000000000000000000000000000000c4",
        )
        self.assertEqual(
            protocol.rgb(0, 255, 0).hex(),
            "33050d00ff0000000000000000000000000000c4",
        )

    def test_color_temperature_frame_matches_capture(self) -> None:
        frame = protocol.color_temperature(5900, (255, 243, 234))
        self.assertEqual(
            frame.hex(),
            "33050dffffff170cfff3ea000000000000000039",
        )

    def test_checksum_is_xor_of_first_nineteen_bytes(self) -> None:
        for frame in (protocol.power(True), protocol.rgb(1, 2, 3)):
            self.assertEqual(len(frame), 20)
            self.assertEqual(frame[19], protocol.checksum(frame[:19]))

    def test_query_frame(self) -> None:
        self.assertEqual(
            protocol.query_power().hex(),
            "aa010000000000000000000000000000000000ab",
        )

    def test_kelvin_to_rgb_is_clamped(self) -> None:
        for kelvin in (2700, 2900, 5900, 6500):
            tint = protocol.kelvin_to_rgb(kelvin)
            self.assertEqual(len(tint), 3)
            self.assertTrue(all(0 <= value <= 255 for value in tint))

    def test_advertisement_autodetect(self) -> None:
        match = protocol.match_advertisement("ihoment_H6010_XXXX")
        self.assertIsNotNone(match)
        self.assertEqual(match.model, "H6010")
        self.assertEqual(match.name, "ihoment_H6010_XXXX")
        self.assertIsNone(protocol.match_advertisement("Govee_H6102_1234"))
        self.assertIsNone(protocol.match_advertisement(None))

    def test_captured_kelvin_tints(self) -> None:
        self.assertEqual(protocol.kelvin_to_rgb(5900), (255, 243, 234))
        self.assertEqual(protocol.kelvin_to_rgb(2900), (255, 182, 98))

    def test_parse_status_replies_from_capture(self) -> None:
        self.assertEqual(
            protocol.parse_status(bytes.fromhex("aa010100000000000000000000000000000000aa")),
            {"is_on": True},
        )
        self.assertEqual(
            protocol.parse_status(bytes.fromhex("aa046400000000000000000000000000000000ca")),
            {"brightness": 100},
        )
        self.assertEqual(
            protocol.parse_status(bytes.fromhex("aa050dffb16d0bb8000000000000000000000032")),
            {"kelvin": 3000},
        )
        self.assertEqual(
            protocol.parse_status(bytes.fromhex("aa050dff00000000000000000000000000000000")),
            None,
        )

    def test_out_of_range_values_rejected(self) -> None:
        with self.assertRaises(ValueError):
            protocol.brightness(101)
        with self.assertRaises(ValueError):
            protocol.color_temperature(1000, (255, 255, 255))
        with self.assertRaises(ValueError):
            protocol.rgb(256, 0, 0)


if __name__ == "__main__":
    unittest.main()
