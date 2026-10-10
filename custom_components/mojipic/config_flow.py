"""Config flow for the MojiPic integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

from .api import MojipicApi, MojipicApiError
from .const import DEFAULT_SCAN_TIMEOUT, DOMAIN


def _schema(default_address: str | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("address", default=default_address or ""): selector.TextSelector(),
            vol.Optional("name", default="Mojipic"): selector.TextSelector(),
            vol.Optional("slot", default=0): vol.Coerce(int),
            vol.Optional("scan", default=False): selector.BooleanSelector(),
        }
    )


class MojipicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MojiPic."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            address = (user_input.get("address") or "").strip().upper()

            if user_input.get("scan") and not address:
                # Let HA's ongoing bluetooth scan find a Mojipic for us.
                api = MojipicApi(self.hass, address="")
                try:
                    found = await api.async_find_device(DEFAULT_SCAN_TIMEOUT)
                except MojipicApiError:
                    found = None
                if not found:
                    errors["base"] = "not_found"
                else:
                    address = found
                    user_input["name"] = user_input.get("name") or api.name

            if address and "base" not in errors:
                await self.async_set_unique_id(address.lower())
                self._abort_if_unique_id_configured()

                info = {"address": address, "name": user_input.get("name") or "Mojipic"}
                try:
                    api = MojipicApi(self.hass, address, info["name"],
                                     int(user_input.get("slot", 0)))
                    info["details"] = await api.async_read_info()
                except MojipicApiError as err:
                    # Non-fatal: the device may simply be asleep. Let it save.
                    info["details"] = {"error": str(err)}

                return self.async_create_entry(title=info["name"], data=info)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema((user_input or {}).get("address")),
            errors=errors,
        )
