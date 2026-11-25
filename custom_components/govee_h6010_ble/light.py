"""The Govee H6010 BLE light entity."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
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
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    MAX_BRIGHTNESS,
    MAX_KELVIN,
    MIN_BRIGHTNESS,
    MIN_KELVIN,
    NOTIFY_UUID,
    WRITE_HANDLE,
    WRITE_UUID,
)
from .protocol import (
    brightness,
    color_temperature,
    kelvin_to_rgb,
    parse_status,
    power,
    query_brightness,
    query_color,
    query_power,
    rgb,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
CONNECT_TIMEOUT = 15.0
KEEPALIVE_INTERVAL = 10.0
# The app polls only occasionally; a five minute interval is enough to pick up
# changes made through other controllers without keeping the radio busy.
SCAN_INTERVAL = timedelta(minutes=5)
REPLY_TIMEOUT = 2.0


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
                entry.data.get(CONF_NAME) or "Govee Light",
                entry.entry_id,
                entry.data.get(CONF_MODEL, "H6010"),
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
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        entry_id: str,
        model: str = "H6010",
    ) -> None:
        self.hass = hass
        self._address = address
        self._attr_unique_id = f"{address}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="Govee",
            model=model,
        )
        self._client: BleakClient | None = None
        self._write_char: BleakGATTCharacteristic | str | None = None
        self._notify_char: BleakGATTCharacteristic | str | None = None
        self._connect_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._state_replies: dict[int, bytes] = {}
        self._state_event = asyncio.Event()
        self._keepalive_task: asyncio.Task | None = None

    async def async_added_to_hass(self) -> None:
        """Open the connection early so the first control is not delayed."""
        self.hass.async_create_task(self._async_preconnect())

    async def _async_preconnect(self) -> None:
        await asyncio.sleep(2)
        try:
            await self._async_client()
        except HomeAssistantError:
            pass

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting brightness, RGB or color temperature."""
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
        except HomeAssistantError as err:
            # Keep the optimistic state. Rolling it back makes the UI flash a
            # wrong value when another controller briefly holds the light.
            _LOGGER.debug("Command to %s failed: %s", self._address, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self._attr_is_on = False
        self.async_write_ha_state()
        try:
            await self._async_send([power(False)])
        except HomeAssistantError as err:
            _LOGGER.debug("Power off for %s failed: %s", self._address, err)

    async def async_update(self) -> None:
        """Read the real state occasionally, e.g. after changes made elsewhere.

        Only power is queried while the light is off. Brightness and color are
        queried when it is on, so polling stays cheap.
        """
        try:
            client = await self._async_client()
        except HomeAssistantError:
            return

        expected = {0x01}
        if self._attr_is_on:
            expected.update({0x04, 0x05})

        self._state_replies.clear()
        self._state_event.clear()
        try:
            async with self._io_lock:
                for frame in (
                    [query_power(), query_brightness(), query_color()]
                    if self._attr_is_on
                    else [query_power()]
                ):
                    await client.write_gatt_char(
                        self._write_char or WRITE_UUID, frame, response=False
                    )
            deadline = asyncio.get_running_loop().time() + REPLY_TIMEOUT
            while not expected <= self._state_replies.keys():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                self._state_event.clear()
                await asyncio.wait_for(self._state_event.wait(), remaining)
        except (BleakError, TimeoutError, OSError, asyncio.TimeoutError):
            await self._async_disconnect()
            return

        self._apply_status_replies()

    def _apply_status_replies(self) -> None:
        """Merge parsed replies into the entity state."""
        for command, frame in list(self._state_replies.items()):
            status = parse_status(frame)
            if status is None:
                continue
            if command == 0x01:
                self._attr_is_on = bool(status.get("is_on"))
            elif command == 0x04:
                percent = int(status.get("brightness", 0))
                if MIN_BRIGHTNESS <= percent <= MAX_BRIGHTNESS:
                    self._attr_brightness = round(percent * 255 / 100)
            elif command == 0x05:
                if "kelvin" in status:
                    self._attr_color_temp_kelvin = int(status["kelvin"])
                    self._attr_color_mode = ColorMode.COLOR_TEMP
                elif "rgb" in status:
                    self._attr_rgb_color = tuple(status["rgb"])
                    self._attr_color_mode = ColorMode.RGB
        self.async_write_ha_state()

    def _notification_handler(self, _characteristic: Any, data: bytearray) -> None:
        """Collect status replies sent by the light."""
        frame = bytes(data)
        if len(frame) >= 3 and frame[0] == 0xAA:
            self._state_replies[frame[1]] = frame
            self._state_event.set()

    async def _async_send(self, frames: list[bytes]) -> None:
        """Write frames over a reused connection, retrying once on failure."""
        for attempt in range(2):
            client = await self._async_client()
            try:
                async with self._io_lock:
                    for frame in frames:
                        await client.write_gatt_char(
                            self._write_char or WRITE_UUID, frame, response=False
                        )
                return
            except (BleakError, TimeoutError, OSError) as err:
                await self._async_disconnect(force=True)
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
            self._write_char = _find_write_characteristic(client)
            self._notify_char = _find_notify_characteristic(client)
            if self._notify_char is not None:
                try:
                    await client.start_notify(
                        self._notify_char, self._notification_handler
                    )
                except (BleakError, OSError):
                    _LOGGER.debug("Status notifications unavailable for %s", self._address)
                    self._notify_char = None
            self._keepalive_task = self.hass.async_create_task(
                self._async_keepalive(client)
            )
            return client

    async def _async_keepalive(self, client: BleakClient) -> None:
        """Send the app's lightweight power query so idle proxies keep the link."""
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if self._client is not client:
                    return
                async with self._io_lock:
                    await client.write_gatt_char(
                        self._write_char or WRITE_UUID,
                        query_power(),
                        response=False,
                    )
        except asyncio.CancelledError:
            return
        except (BleakError, TimeoutError, OSError):
            await self._async_disconnect(force=True)

    async def _async_disconnect(self, *, force: bool = False) -> None:
        client = self._client
        self._client = None
        self._write_char = None
        self._notify_char = None
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if client is None or force:
            # On a failed link, awaiting bleak's disconnect can hang for its
            # own 20s timeout. Drop the reference and let the stack clean up.
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
    """Prefer the documented Govee write characteristic."""
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


def _find_notify_characteristic(
    client: BleakClient,
) -> BleakGATTCharacteristic | None:
    """Find the status notification characteristic."""
    try:
        characteristics = [
            char for service in client.services for char in service.characteristics
        ]
    except (BleakError, AttributeError):
        return None
    for char in characteristics:
        if str(char.uuid).lower() == NOTIFY_UUID.lower():
            return char
    for char in characteristics:
        if char.handle == 0x000E and "notify" in char.properties:
            return char
    for char in characteristics:
        if "notify" in char.properties:
            return char
    return None
