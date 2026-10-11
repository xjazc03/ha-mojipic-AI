"""Select entity: pick a stored image without writing YAML.

This is what makes the integration usable from a dashboard. `current_option` is
the id of the displayed image; selecting one pushes it immediately.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant import config_entries
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_NAME,
    DEFAULT_NAME_PREFIX,
    DOMAIN,
    ISSUE_UPLOAD_IMAGE,
    UPLOAD_OPTION,
)
from .image_encode import ImageConversionError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    entity = MojipicImageSelect(hass, entry, bundle)
    # Load the option list BEFORE async_add_entities, because populating it
    # used to be what triggered the first async_write_ha_state -- and an
    # entity that has not been added yet has no entity_id, so that raises and
    # takes the whole select platform down with it. The entity disappears from
    # the dashboard entirely, while the other platforms keep working, which
    # makes it look like the entity was deleted rather than broken.
    await entity.async_refresh_options(write_state=False)
    async_add_entities([entity])
    entity._added = True
    entity.async_write_ha_state()

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            entity.async_refresh_options_hack,
            # Must be the stdlib datetime.timedelta:
            # async_track_time_interval calls interval.total_seconds(), and
            # homeassistant.util.dt does not export a timedelta.
            timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
    )


class MojipicImageSelect(SelectEntity):
    """Choose which stored image is on the display."""

    _attr_should_poll = False
    _attr_translation_key = "image_select"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        super().__init__()
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
        # Set once async_add_entities has run. Writing state before that is
        # what made this entity vanish, so every write goes through a guard.
        self._added: bool = False
        # Picked up right after the upload flow stores a new image.
        entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"{DOMAIN}_{entry.entry_id}_images_changed",
                self._handle_images_changed,
            )
        )

    async def async_refresh_options_hack(self, _now=None) -> None:
        await self.async_refresh_options()

    @callback
    def _handle_images_changed(self, *_args) -> None:
        """Re-read the stored list when the upload flow adds an image."""
        self.hass.async_create_task(self.async_refresh_options())

    async def async_refresh_options(self, write_state: bool = True) -> None:
        """Pick up newly uploaded images without a restart."""
        entries = await self.manager.async_list()
        # The upload entry always sorts first so it is where the eye lands.
        options = [UPLOAD_OPTION] + [str(item["id"]) for item in entries]
        if options != self._options:
            self._options = options
            if self._current and self._current not in options:
                self._current = None
            # Only write state once the entity is actually registered. An
            # unadded entity has no entity_id and this raises.
            if write_state and self._added:
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
        """Push the chosen image, or open the upload flow."""
        if option == UPLOAD_OPTION:
            self._async_open_upload_flow()
            return

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


    def _async_open_upload_flow(self) -> None:
        """Open the upload repair flow.

        Repairs are the documented way for an entity to open a flow that needs
        input: create a fixable issue, then start its flow. The user gets a
        file picker, so no YAML is needed.
        """
        from homeassistant.helpers import issue_registry as ir

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_UPLOAD_IMAGE,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UPLOAD_IMAGE,
            data={"entry_id": self.entry.entry_id},
        )
        ir.async_get_issue(
            self.hass, DOMAIN, ISSUE_UPLOAD_IMAGE
        ).async_create_flow(
            ISSUE_UPLOAD_IMAGE,
            context={
                "source": config_entries.SOURCE_REPAIR,
                "entry_id": self.entry.entry_id,
            },
        )
        # Put the dropdown back to whatever it was showing; the upload is a
        # side action, not a selection.
        self.async_write_ha_state()
