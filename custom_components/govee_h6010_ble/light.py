"""The Govee H6010 BLE light entity."""

from __future__ import annotations

import asyncio
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
CONNECT_TIMEOUT = 5.0


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

    One connection is kept open and reused for every command. Opening a new
    BLE connection for each service call is what makes lights feel sluggish,
    especially through a Bluetooth proxy. If the connection drops, it is
    rebuilt from the best currently available adapter or proxy.
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
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting brightness, RGB or color temperature."""
        previous = (
            self._attr_is_on,
            self._attr_brightness,
            self._attr_rgb_color,
            self._attr_color_temp_kelvin,
            self._attr_color_mode,
        )
        frames: list[bytes] = []
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
            kelvin = max(
                MIN_KELVIN, min(int(kwargs[ATTR_COLOR_TEMP_KELVIN]), MAX_KELVIN)
            )
            frames.append(color_temperature(kelvin, kelvin_to_rgb(kelvin)))
            self._attr_color_temp_kelvin = kelvin
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if not frames:
            frames.append(power(True))

        self._attr_is_on = True
        # Reflect the change in the UI before waiting on the radio.
        self.async_write_ha_state()
        try:
            await self._async_send(frames)
        except HomeAssistantError:
            (
                self._attr_is_on,
                self._attr_brightness,
                self._attr_rgb_color,
                self._attr_color_temp_kelvin,
                self._attr_color_mode,
            ) = previous
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        was_on = self._attr_is_on
        self._attr_is_on = False
        self.async_write_ha_state()
        try:
            await self._async_send([power(False)])
        except HomeAssistantError:
            self._attr_is_on = was_on
            self.async_write_ha_state()
            raise

    async def _async_send(self, frames: list[bytes]) -> None:
        """Write frames over a reused connection, retrying once on failure."""
        for attempt in range(2):
            client = await self._async_client()
            try:
                characteristic = _find_write_characteristic(client)
                for frame in frames:
                    await client.write_gatt_char(
                        characteristic, frame, response=False
                    )
                return
            except (BleakError, TimeoutError, OSError) as err:
                await self._async_disconnect()
                if attempt:
                    raise HomeAssistantError(
                        f"Failed to write to {self._address}: {err}"
                    ) from err

    async def _async_client(self) -> BleakClient:
        """Return a connected client, opening one only when necessary."""
        if self._client is not None and self._client.is_connected:
            return self._client

        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return self._client

            device = async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if device is None:
                raise HomeAssistantError(
                    f"No connectable BLE device found for {self._address}. "
                    "Check that a Bluetooth adapter or proxy is in range."
                )

            client = BleakClient(device, timeout=CONNECT_TIMEOUT)
            try:
                await client.connect()
            except (BleakError, TimeoutError, OSError) as err:
                raise HomeAssistantError(
                    f"Could not connect to {self._address}: {err}"
                ) from err
            self._client = client
            return client

    async def _async_disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except (BleakError, OSError):
            pass

    async def async_will_remove_from_hass(self) -> None:
        """Release the BLE connection when the entity is removed."""
        await self._async_disconnect()


def _find_write_characteristic(
    client: BleakClient,
) -> BleakGATTCharacteristic | str:
    """Prefer the documented Govee write characteristic.

    Falls back to the observed handle, then to any writable characteristic,
    so minor firmware/UUID differences do not break the entity.
    """
    try:
        characteristics = [
            char for service in client.services for char in service.characteristics
        ]
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
