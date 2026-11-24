"""The Govee H6010 BLE light entity."""

from __future__ import annotations

from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    MAX_BRIGHTNESS,
    MAX_KELVIN,
    MIN_BRIGHTNESS,
    MIN_KELVIN,
    WRITE_HANDLE,
    WRITE_UUID,
)
from .protocol import brightness, color_temperature, kelvin_to_rgb, power, rgb

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the discovered light."""
    async_add_entities(
        [
            GoveeH6010Light(
                hass,
                entry.data[CONF_ADDRESS],
                entry.data.get(CONF_NAME) or "Govee H6010",
                entry.entry_id,
            )
        ]
    )


class GoveeH6010Light(LightEntity):
    """A single H6010, controlled directly over BLE.

    Works with a local Bluetooth adapter and with ESPHome Bluetooth proxies,
    because the BLEDevice comes from Home Assistant's bluetooth integration.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.RGB
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN
    _attr_brightness = 255
    _attr_rgb_color = (255, 255, 255)
    _attr_color_temp_kelvin = MIN_KELVIN
    _attr_is_on = False

    def __init__(
        self, hass: HomeAssistant, address: str, name: str, entry_id: str
    ) -> None:
        self.hass = hass
        self._address = address
        self._attr_unique_id = f"{address}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="Govee",
            model="H6010",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting brightness, RGB or color temperature."""
        frames = []
        if not self._attr_is_on:
            frames.append(power(True))

        if ATTR_BRIGHTNESS in kwargs:
            percent = round(int(kwargs[ATTR_BRIGHTNESS]) * 100 / 255)
            percent = max(MIN_BRIGHTNESS, min(percent, MAX_BRIGHTNESS))
            frames.append(brightness(percent))
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]

        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            frames.append(rgb(red, green, blue))
            self._attr_rgb_color = (red, green, blue)
            self._attr_color_mode = ColorMode.RGB

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(MIN_KELVIN, min(int(kwargs[ATTR_COLOR_TEMP_KELVIN]), MAX_KELVIN))
            tint = kelvin_to_rgb(kelvin)
            frames.append(color_temperature(kelvin, tint))
            self._attr_color_temp_kelvin = kelvin
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if not frames:
            frames.append(power(True))

        await self._async_send(frames)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_send([power(False)])
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_send(self, frames: list[bytes]) -> None:
        """Open one BLE connection and write all frames through it."""
        device = async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if device is None:
            raise HomeAssistantError(
                f"No connectable BLE device found for {self._address}. "
                "Check that a Bluetooth adapter or proxy is in range."
            )

        try:
            async with BleakClient(device, timeout=10.0) as client:
                characteristic = _find_write_characteristic(client)
                for frame in frames:
                    await client.write_gatt_char(characteristic, frame, response=False)
        except (BleakError, TimeoutError, OSError) as err:
            raise HomeAssistantError(
                f"Failed to write to {self._address}: {err}"
            ) from err


def _find_write_characteristic(
    client: BleakClient,
) -> BleakGATTCharacteristic | str:
    """Prefer the documented Govee write characteristic.

    Falls back to the observed handle, then to any writable characteristic,
    so minor firmware/UUID differences do not break the entity.
    """
    try:
        characteristics = [char for service in client.services for char in service.characteristics]
    except (BleakError, AttributeError):
        return WRITE_UUID

    for char in characteristics:
        if str(char.uuid).lower() == WRITE_UUID.lower():
            return char
    for char in characteristics:
        if char.handle == WRITE_HANDLE and "write" in char.properties:
            return char
    for char in characteristics:
        if "write" in char.properties:
            return char
    return WRITE_UUID
