"""Config and repair flows for MojiPic.

Two things this gets right that a barebones config flow would not:

  * Errors say **what to check**, not a stack trace. MojiPic users are usually
    losing because the display is off, out of range, or still connected to the
    vendor app -- all of which are fixable and worth naming.
  * There is a real repair path. A display that was working yesterday should not
    require remove-and-re-add just because its address stopped answering.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

# Version-fragile imports, guarded deliberately.
#
# `ConfigFlowResult` only exists on newer Home Assistant, and the repair flow
# class has been renamed between versions. Importing either unguarded means a
# version skew turns into "Config flow could not be loaded: 500" -- a failure
# that looks like a server problem and tells the user nothing. Degrade
# gracefully instead: the annotations are hints, not runtime requirements.
try:  # Home Assistant >= 2024.4
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover - older cores
    ConfigFlowResult = Any  # type: ignore[misc, assignment]

try:  # renamed from RepairsFlow on newer cores
    _RepairFlowBase = config_entries.RepairFlow
except AttributeError:  # pragma: no cover - older cores
    try:
        _RepairFlowBase = config_entries.RepairsFlow
    except AttributeError:
        # Neither name exists. The repair flow is a convenience, so degrade to
        # a plain object rather than taking the config flow down with it.
        _RepairFlowBase = object

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    CONF_NAME_PREFIX,
    CONF_SCAN,
    DEFAULT_NAME_PREFIX,
    DISCOVERY_TIMEOUT,
    DOMAIN,
)
from .transport import TransportError, default_transport

_LOGGER = logging.getLogger(__name__)

_NOT_FOUND_HELP = (
    "Check that the display is powered on, within Bluetooth range of the "
    "adapter Home Assistant is using, and NOT connected to the MojiPic vendor "
    "app -- Android and iOS apps often hold a BLE connection open, and this "
    "display only allows one at a time."
)

_NO_BLUETOOTH_HELP = (
    "Home Assistant needs a Bluetooth adapter it can reach. Options: a local "
    "adapter (Home Assistant OS: Settings -> System -> Hardware, or an add-on "
    "with host network access), or an ESPHome Bluetooth proxy placed near the "
    "display."
)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN, default=defaults.get(CONF_SCAN, True)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ADDRESS, default=defaults.get(CONF_ADDRESS, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME_PREFIX)
            ): selector.TextSelector(),
            vol.Optional(
                CONF_NAME_PREFIX,
                default=defaults.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX),
            ): selector.TextSelector(),
        }
    )


async def _async_bluetooth_available(hass) -> bool:
    try:
        from homeassistant.components import bluetooth
    except ImportError:  # pragma: no cover
        return False
    return getattr(bluetooth, "async_scanner_count", lambda: 0)() > 0


class MojipicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            address = (user_input.get(CONF_ADDRESS) or "").strip().upper()
            prefix = (user_input.get(CONF_NAME_PREFIX) or DEFAULT_NAME_PREFIX).strip()

            if user_input.get(CONF_SCAN) and not address:
                if not await _async_bluetooth_available(self.hass):
                    errors["base"] = "no_bluetooth"
                else:
                    transport = default_transport(self.hass)
                    try:
                        found = await transport.async_discover(
                            prefix, DISCOVERY_TIMEOUT
                        )
                    except TransportError:
                        found = None
                    if not found:
                        errors["base"] = "not_found"
                    else:
                        address = found

            if address and "base" not in errors:
                if not _valid_address(address):
                    errors["base"] = "invalid_address"
                else:
                    await self.async_set_unique_id(address.lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=user_input.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
                        data={
                            CONF_ADDRESS: address,
                            CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
                            CONF_NAME_PREFIX: prefix,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
            description_placeholders={"help_not_found": _NOT_FOUND_HELP},
        )

    async def async_step_bluetooth(self, discovery_info) -> ConfigFlowResult:
        """Handle a device discovered by HA's Bluetooth integration."""
        address = discovery_info.get("address") if discovery_info else None
        if not address:
            return self.async_abort(reason="no_devices_found")
        await self.async_set_unique_id(address.lower())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=DEFAULT_NAME_PREFIX,
            data={
                CONF_ADDRESS: address.upper(),
                CONF_NAME: DEFAULT_NAME_PREFIX,
                CONF_NAME_PREFIX: DEFAULT_NAME_PREFIX,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return MojipicOptionsFlow(entry)


def _valid_address(address: str) -> bool:
    parts = address.split(":")
    if len(parts) != 6:
        return False
    return all(len(part) == 2 and all(c in "0123456789abcdefABCDEF" for c in part)
               for part in parts)


class MojipicOptionsFlow(config_entries.OptionsFlow):
    """Let the user rename the device or change the discovery prefix."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input},
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                {
                    CONF_ADDRESS: self.config_entry.data.get(CONF_ADDRESS),
                    CONF_NAME: self.config_entry.data.get(CONF_NAME),
                    CONF_NAME_PREFIX: self.config_entry.data.get(
                        CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX
                    ),
                }
            ),
        )


class MojipicRepairFlow(_RepairFlowBase):
    """Recover a display that has stopped responding, without re-adding it."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            coordinator = self.hass.data[DOMAIN][entry.entry_id]["coordinator"]
            new_address = await coordinator.async_rebind_if_missing()
            if new_address:
                return self.async_create_entry(title="", data={})
            return self.async_abort(reason="still_unreachable")

        return self.async_show_form(
            step_id="init",
            description_placeholders={"help": _NOT_FOUND_HELP},
        )
