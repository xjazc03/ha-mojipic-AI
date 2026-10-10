"""Image entity for MojiPic — shows whatever was last sent to the panel."""

from __future__ import annotations

import io
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.util import dt as dt_util

from .api import MojipicApi
from .const import ATTR_SLOT, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    api = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MojipicImage(hass, entry, api)])


class MojipicImage(ImageEntity):
    """The most recently displayed image."""

    _attr_should_poll = False
    _attr_translation_key = "image"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MojipicApi) -> None:
        self.hass = hass
        self.api = api
        self._entry = entry
        self._attr_unique_id = f"{entry.data['address'].lower()}_image"
        self._attr_name = f"{entry.data.get('name') or 'Mojipic'} image"
        self._attr_device_info = self._device_info(entry)
        self._cached: bytes | None = None
        self._content_type: str | None = None
        self._last_updated: str | None = None

    @staticmethod
    def _device_info(entry: ConfigEntry) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"].lower())},
            name=entry.data.get("name") or "Mojipic",
            manufacturer="Infinitum Trading (IIP)",
            model="MojiPic 32x32 RGB LED matrix",
            connections={
                (CONNECTION_BLUETOOTH, entry.data["address"].upper())
            },
        )

    @callback
    def async_set_image(self, image_bytes: bytes, content_type: str) -> None:
        """Called by the display_image service after a successful push."""
        self._cached = image_bytes
        self._content_type = content_type
        self._last_updated = dt_util.utcnow().isoformat()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"address": self.api.address}
        if self._last_updated:
            attrs["last_updated"] = self._last_updated
        if self.api.slot is not None:
            attrs["slot"] = self.api.slot
        return attrs

    @property
    def available(self) -> bool:
        return True

    async def async_image(self) -> bytes | None:
        """Return the cached image bytes."""
        return self._cached

    @property
    def content_type(self) -> str:
        return self._content_type or "image/png"

    @property
    def image_last_updated(self) -> str | None:
        return self._last_updated
