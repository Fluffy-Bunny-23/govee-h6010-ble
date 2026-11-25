"""Config flow for Govee H6010 BLE.

Automatic discovery comes from the manifest's bluetooth matcher on the
advertised local name "ihoment_H6010_*". A manual step is kept as a fallback
for setups where Home Assistant cannot see the advertisement directly but a
Bluetooth proxy can.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.config_flow import BluetoothConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_ADDRESS, CONF_MODEL, CONF_NAME, DOMAIN, LOCAL_NAME_PREFIX
from .protocol import match_advertisement

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class GoveeH6010BLEConfigFlow(BluetoothConfigFlow, domain=DOMAIN):
    """Discover or manually add an H6010 light.

    The ``domain=`` keyword registers the handler with Home Assistant. Without
    it the Add Integration dialog reports "Invalid handler specified".
    """

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._address: str | None = None
        self._name: str | None = None
        self._model: str = "H6010"

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle an advertisement matched by the manifest."""
        match = match_advertisement(
            discovery_info.local_name or discovery_info.name
        )
        if match is None:
            return self.async_abort(reason="not_supported")

        self._address = discovery_info.address.upper()
        self._name = match.name
        self._model = match.model
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a discovered light."""
        if user_input is not None:
            return self._create_entry()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name or "H6010"},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manually add a light by MAC address and advertised name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            name = user_input.get(CONF_NAME) or LOCAL_NAME_PREFIX
            match = match_advertisement(name)
            if not MAC_RE.match(address):
                errors["base"] = "invalid_address"
            elif match is None:
                errors["base"] = "not_supported"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = match.name
                self._model = match.model
                return self._create_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=LOCAL_NAME_PREFIX): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    def _create_entry(self) -> FlowResult:
        return self.async_create_entry(
            title=self._name or "Govee H6010",
            data={
                CONF_ADDRESS: self._address,
                CONF_NAME: self._name,
                CONF_MODEL: self._model,
            },
        )
