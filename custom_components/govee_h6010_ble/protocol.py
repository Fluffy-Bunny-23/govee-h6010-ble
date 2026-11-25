"""Govee BLE frame construction, based on a captured H6010 session.

Every frame is 20 bytes:

    byte 0      0x33 for a write, 0xAA for a query
    byte 1      command
    bytes 2-18  payload, zero padded
    byte 19     XOR of bytes 0-18

The frames in tests/test_protocol.py were captured from a Govee Home app
controlling an H6010 over BLE while the tablet was in airplane mode.
"""

from __future__ import annotations

from dataclasses import dataclass

# This module is deliberately standalone so the frame builders can be unit
# tested without Home Assistant installed. const.py re-exports these values.
LOCAL_NAME_PREFIX = "ihoment_H6010_"
MIN_KELVIN = 2700
MAX_KELVIN = 6500
MIN_BRIGHTNESS = 1
MAX_BRIGHTNESS = 100

# Exact tints taken from the app's own frames. The curve fit below is close,
# but these two values make the captured frames reproduce byte-for-byte.
KELVIN_TINT_OVERRIDES = {
    2900: (255, 182, 98),
    5900: (255, 243, 234),
}

FRAME_LENGTH = 20
WRITE = 0x33
QUERY = 0xAA

CMD_POWER = 0x01
CMD_BRIGHTNESS = 0x04
CMD_COLOR = 0x05

COLOR_MODE_RGBWW = 0x0D
QUERY_POWER = 0x01


def checksum(body: bytes | bytearray) -> int:
    """XOR of the first 19 bytes."""
    value = 0
    for byte in body:
        value ^= byte
    return value


def frame(command: int, payload: bytes = b"", *, read: bool = False) -> bytes:
    """Build a 20-byte Govee frame with its trailing BCC."""
    head = QUERY if read else WRITE
    body = bytearray(FRAME_LENGTH - 1)
    body[0] = head
    body[1] = command
    if len(payload) > FRAME_LENGTH - 3:
        raise ValueError("payload too long")
    body[2 : 2 + len(payload)] = payload
    return bytes(body + bytes([checksum(body)]))


def power(on: bool) -> bytes:
    return frame(CMD_POWER, bytes([int(on)]))


def brightness(percent: int) -> bytes:
    """H6010 takes a percentage, confirmed by captured 19% and 73% frames."""
    if not MIN_BRIGHTNESS <= percent <= MAX_BRIGHTNESS:
        raise ValueError(f"brightness must be {MIN_BRIGHTNESS}-{MAX_BRIGHTNESS}")
    return frame(CMD_BRIGHTNESS, bytes([percent]))


def rgb(red: int, green: int, blue: int) -> bytes:
    """Whole-device static color, sub-mode 0x0D with zero Kelvin fields."""
    for value in (red, green, blue):
        if not 0 <= value <= 255:
            raise ValueError("RGB components must be 0-255")
    return frame(CMD_COLOR, bytes([COLOR_MODE_RGBWW, red, green, blue]))


def color_temperature(kelvin: int, tint: tuple[int, int, int]) -> bytes:
    """Set white temperature.

    The app sends the RGB rendering of the Kelvin value in the trailing white
    triplet, with Kelvin itself big-endian in bytes 6 and 7.
    """
    if not MIN_KELVIN <= kelvin <= MAX_KELVIN:
        raise ValueError(f"kelvin must be {MIN_KELVIN}-{MAX_KELVIN}")
    k_hi, k_lo = kelvin >> 8, kelvin & 0xFF
    return frame(
        CMD_COLOR,
        bytes(
            [
                COLOR_MODE_RGBWW,
                0xFF,
                0xFF,
                0xFF,
                k_hi,
                k_lo,
                tint[0],
                tint[1],
                tint[2],
            ]
        ),
    )


def query_power() -> bytes:
    return frame(QUERY_POWER, read=True)


def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Approximate the Govee Kelvin tint (Tanner Helland's curve fit)."""
    import math

    kelvin = max(MIN_KELVIN, min(kelvin, MAX_KELVIN))
    if kelvin in KELVIN_TINT_OVERRIDES:
        return KELVIN_TINT_OVERRIDES[kelvin]

    temp = kelvin / 100.0

    red = 255.0 if temp <= 66 else 329.698727446 * (temp - 60) ** -0.1332047592
    green = (
        99.4708025861 * math.log(temp) - 161.1195681661
        if temp <= 66
        else 288.1221695283 * (temp - 60) ** -0.0755148492
    )
    if temp >= 66:
        blue = 255.0
    elif temp <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(temp - 10) - 305.0447927307

    return tuple(max(0, min(255, round(value))) for value in (red, green, blue))


@dataclass(frozen=True)
class AdvertisementMatch:
    """Result of matching a BLE advertisement to this integration."""

    model: str
    name: str


def match_advertisement(local_name: str | None) -> AdvertisementMatch | None:
    """Auto-detect the light by its advertised name.

    H6010 advertises as e.g. "ihoment_H6010_XXXX". The suffix varies per unit,
    so match the stable prefix rather than a full name or MAC address.
    """
    if not local_name:
        return None
    if not local_name.startswith(LOCAL_NAME_PREFIX):
        return None
    return AdvertisementMatch(model="H6010", name=local_name)
