"""The MojiPic integration.

Registers `mojipic.display_image` plus a few device controls. Built for
event -> image automations rather than exhaustive telemetry, so the entity
surface is deliberately minimal: one image entity, one battery sensor.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, entity_platform

from .api import MojipicApi, MojipicApiError
from .const import DOMAIN
from .protocol import MojipicProtocolError, load_image

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["image", "sensor"]

ATTR_ADDRESS = "address"
ATTR_SLOT = "slot"
ATTR_IMAGE = "image"
ATTR_PATH = "path"
ATTR_URL = "url"
ATTR_BRIGHTNESS = "brightness"


def _entry_for(hass: HomeAssistant, call: ServiceCall) -> tuple[ConfigEntry, MojipicApi]:
    """Resolve which configured device a service call targets."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise MojipicApiError("no MojiPic devices configured")

    address = call.data.get(ATTR_ADDRESS)
    if address:
        wanted = address.upper()
        for entry in entries:
            if entry.data.get(ATTR_ADDRESS, "").upper() == wanted:
                return entry, _api_from_entry(entry)
        raise MojipicApiError(f"no MojiPic configured with address {wanted}")

    entry = entries[0]
    if len(entries) > 1:
        _LOGGER.debug("multiple MojiPic entries; using %s", entry.title)
    return entry, _api_from_entry(entry)


def _api_from_entry(entry: ConfigEntry) -> MojipicApi:
    return MojipicApi(
        entry.hass,
        entry.data[ATTR_ADDRESS],
        entry.data.get("name"),
        int(entry.data.get(ATTR_SLOT, 0)),
    )


async def _async_resolve_image(hass: HomeAssistant, call: ServiceCall):
    """Get a PIL image from whichever of path / url / image the caller used."""
    if ATTR_IMAGE in call.data and call.data[ATTR_IMAGE] is not None:
        raw = call.data[ATTR_IMAGE]
        # The image selector yields bytes already.
        return load_image(raw)

    path = call.data.get(ATTR_PATH)
    if path:
        # Only allow paths inside HA's config directory tree.
        resolved = hass.config.path(path.lstrip("/"))
        allowed = hass.config.config_dir
        if not resolved.startswith(allowed):
            raise MojipicApiError(
                "path must be inside the Home Assistant config directory"
            )
        with open(resolved, "rb") as handle:
            return load_image(handle.read())

    url = call.data.get(ATTR_URL)
    if url:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                resp.raise_for_status()
                return load_image(await resp.read())

    raise MojipicApiError(
        "no image supplied; provide one of 'image', 'path' or 'url'"
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MojiPic from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = _api_from_entry(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register services. Runs once, on first entry setup."""
    if hass.services.has_service(DOMAIN, "display_image"):
        return True

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "display_image",
        {
            vol.Optional(ATTR_ADDRESS): cv.string,
            vol.Optional(ATTR_SLOT, default=0): vol.Coerce(int),
            vol.Optional(ATTR_IMAGE): cv.ensure_list,
            vol.Optional(ATTR_PATH): cv.string,
            vol.Optional(ATTR_URL): cv.string,
            vol.Optional(ATTR_BRIGHTNESS): vol.Coerce(int),
        },
        "async_service_display_image",
    )

    for service, method in (
        ("set_brightness", "async_service_set_brightness"),
        ("device_on", "async_service_device_on"),
        ("device_off", "async_service_device_off"),
        ("screen_on", "async_service_screen_on"),
        ("screen_off", "async_service_screen_off"),
        ("clear_screen", "async_service_clear_screen"),
    ):
        platform.async_register_entity_service(
            service, {vol.Optional(ATTR_ADDRESS): cv.string}, method
        )

    return True


# --- service implementations (bound per entity) ---------------------------


async def async_service_display_image(self, call: ServiceCall) -> None:
    """Push an image to the panel."""
    _, api = _entry_for(self.hass, call)
    image = await _async_resolve_image(self.hass, call)

    brightness = call.data.get(ATTR_BRIGHTNESS)
    if brightness is not None:
        await api.async_set_brightness(brightness)

    slot = call.data.get(ATTR_SLOT, 0)
    _LOGGER.debug("Displaying image on %s slot %s", api.address, slot)
    await api.async_display_image(image, slot=slot)


async def async_service_set_brightness(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_set_brightness(call.data.get(ATTR_BRIGHTNESS, 50))


async def async_service_device_on(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_device_on()


async def async_service_device_off(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_device_off()


async def async_service_screen_on(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_screen_on()


async def async_service_screen_off(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_screen_off()


async def async_service_clear_screen(self, call: ServiceCall) -> None:
    _, api = _entry_for(self.hass, call)
    await api.async_clear_screen()
