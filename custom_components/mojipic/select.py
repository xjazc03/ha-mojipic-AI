"""Select entity: pick a stored image without writing YAML.

This is what makes the integration usable from a dashboard. `current_option` is
the id of the displayed image; selecting one pushes it immediately.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_NAME, DEFAULT_NAME_PREFIX, DOMAIN
from .image_encode import ImageConversionError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    entity = MojipicImageSelect(hass, entry, bundle)
    await entity.async_refresh_options()
    async_add_entities([entity])

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            entity.async_refresh_options_hack,
            # Must be a timedelta: async_track_time_interval calls
            # interval.total_seconds() and a bare float raises AttributeError.
            dt_util.timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
    )


class MojipicImageSelect(SelectEntity):
    """Choose which stored image is on the display."""

    _attr_should_poll = False
    _attr_translation_key = "image_select"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        self.manager = bundle["manager"]
        address = entry.data["address"]
        self._attr_unique_id = f"{address.lower()}_image_select"
        self._attr_name = (
            f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} image"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )
        self._options: list[str] = []
        self._current: str | None = None

    async def async_refresh_options_hack(self, _now=None) -> None:
        await self.async_refresh_options()

    async def async_refresh_options(self) -> None:
        """Pick up newly uploaded images without a restart."""
        entries = await self.manager.async_list()
        options = [str(item["id"]) for item in entries]
        if options != self._options:
            self._options = options
            if self._current and self._current not in options:
                self._current = None
            self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        return self._options

    @property
    def current_option(self) -> str | None:
        return self._current

    @property
    def available(self) -> bool:
        return self.coordinator.link_state == "configured"

    @property
    def extra_state_attributes(self):
        return {"connection_state": self.coordinator.link_state}

    async def async_select_option(self, option: str) -> None:
        """Push the chosen image to the display."""
        try:
            image_id = int(option)
        except (TypeError, ValueError):
            return

        framebuffer = await self.manager.async_framebuffer(image_id)
        if framebuffer is None:
            raise ImageConversionError(f"No stored image with id {image_id}")

        result = await self.coordinator.async_display(framebuffer, image_id)
        if result.outcome.ok:
            self._current = option
            self.async_write_ha_state()
            await self._async_mirror_to_image_entity(image_id)
        elif result.outcome.value == "abandoned":
            _LOGGER.debug(
                "Push of MojiPic image %s was superseded by a newer request",
                image_id,
            )
        else:
            raise HomeAssistantError(
                f"Could not display image {image_id}: {result.error}"
            )

    async def _async_mirror_to_image_entity(self, image_id: int) -> None:
        """Show the pushed image in the image entity, for dashboards."""
        meta = await self.manager.async_get(image_id)
        if meta is None:
            return
        entities = self.hass.data.get(DOMAIN, {}).get(
            self.entry.entry_id, {}
        ).get("image_entities", [])
        if not entities:
            return
        try:
            raw = await self.hass.async_add_executor_job(
                lambda: open(meta["path"], "rb").read()
            )
        except OSError:
            return
        for entity in entities:
            entity.async_set_image(raw, "image/png", image_id)
