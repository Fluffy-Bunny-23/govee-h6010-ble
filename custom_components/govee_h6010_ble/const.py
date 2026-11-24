"""Constants for the Govee H6010 BLE integration."""

from __future__ import annotations

from .protocol import (
    LOCAL_NAME_PREFIX,
    MAX_BRIGHTNESS,
    MAX_KELVIN,
    MIN_BRIGHTNESS,
    MIN_KELVIN,
)

DOMAIN = "govee_h6010_ble"

CONF_ADDRESS = "address"
CONF_NAME = "name"

LOCAL_NAME_MATCHER = "ihoment_H6010_*"

# Observed on a Fire HD 8 in airplane mode with Bluetooth re-enabled:
# write handle 0x0011, notify handle 0x000F (CCCD).
WRITE_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
NOTIFY_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
WRITE_HANDLE = 0x0011
