"""Select entity: pick a stored image without writing YAML.

This is what makes the integration usable from a dashboard. Buttons for
momentary actions (clear, test pattern) and toggles for binary states
(screen, device) — all verified against APK decompilation.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant import config_entries
from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from pathlib import Path

from .const import (
    CONF_NAME,
    DEFAULT_NAME_PREFIX,
    DOMAIN,
    UPLOAD_OPTION,
    TEST_PATTERN_OPTION,
    URL_DOWNLOAD_OPTION,
)
from .image_encode import ImageConversionError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    # Toggles for binary states (verified APK ScreenHandler): screen, device
    # Buttons for momentary actions: clear, test pattern
    entities = [
        MojipicScreenToggle(hass, entry, bundle, "screen", "Screen"),
        MojipicDeviceToggle(hass, entry, bundle, "device", "Device"),
        MojipicClearButton(hass, entry, bundle),
        MojipicTestButton(hass, entry, bundle),
    ]
    select = MojipicImageSelect(hass, entry, bundle)
    await select.async_refresh_options(write_state=False)
    async_add_entities(entities + [select])
    select._added = True
    select.async_write_ha_state()

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            select.async_options_tick,
            timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
    )


class MojipicScreenToggle(SwitchEntity):
    """Toggle for screen on/off state (APK ScreenHandler: turnOnScreen/turnOffScreen)."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle, cmd_type: str, name: str) -> None:
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        address = entry.data["address"]
        self.cmd_type = cmd_type
        self._attr_unique_id = f"{address.lower()}_{cmd_type}"
        self._attr_name = f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.link_state == "configured"

    @property
    def is_on(self) -> bool | None:
        # Screen state tracked by coordinator through last command result.
        # Default False (off) until a successful turn-on is confirmed.
        return getattr(self.coordinator, "_screen_on", False)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_screen_on()
        self.coordinator._screen_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_screen_off()
        self.coordinator._screen_on = False
        self.async_write_ha_state()


class MojipicDeviceToggle(SwitchEntity):
    """Toggle for device power on/off (APK ScreenHandler: turnOnDevice/turnOffDevice)."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle, cmd_type: str, name: str) -> None:
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        address = entry.data["address"]
        self.cmd_type = cmd_type
        self._attr_unique_id = f"{address.lower()}_{cmd_type}"
        self._attr_name = f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.link_state == "configured"

    @property
    def is_on(self) -> bool | None:
        return getattr(self.coordinator, "_device_on", False)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_device_on()
        self.coordinator._device_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_device_off()
        self.coordinator._device_on = False
        self.async_write_ha_state()


class MojipicClearButton(ButtonEntity):
    """Button to clear screen (APK ScreenHandler: clearScreen)."""

    _attr_should_poll = False
    _attr_icon = "mdi:eraser"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        address = entry.data["address"]
        self._attr_unique_id = f"{address.lower()}_clear"
        self._attr_name = f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} Clear screen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.link_state == "configured"

    async def async_press(self) -> None:
        await self.coordinator.async_clear_screen()


class MojipicTestButton(ButtonEntity):
    """Button to push checkerboard test pattern (APK verified)."""

    _attr_should_poll = False
    _attr_icon = "mdi:checkerboard"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        self.manager = bundle["manager"]
        address = entry.data["address"]
        self._attr_unique_id = f"{address.lower()}_test"
        self._attr_name = f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} Test pattern"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.link_state == "configured"

    async def async_press(self) -> None:
        from PIL import Image
        import io
        size = 32
        img = Image.new("RGB", (size, size), "black")
        pixels = img.load()
        for y in range(size):
            for x in range(size):
                if (x // 4 + y // 4) % 2 == 0:
                    pixels[x, y] = (255, 255, 255)
        raw = io.BytesIO()
        img.save(raw, format="PNG")
        stored = await self.manager.async_upload(raw.getvalue(), "test_pattern.png")
        framebuffer = await self.manager.async_framebuffer(stored["id"])
        result = await self.coordinator.async_display(framebuffer, stored["id"])
        if result.outcome.ok:
            await self.manager.async_save()


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
        self._added: bool = False
        entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"{DOMAIN}_{entry.entry_id}_images_changed",
                self._handle_images_changed,
            )
        )

    async def async_options_tick(self, _now=None) -> None:
        await self.async_refresh_options()

    @callback
    def _handle_images_changed(self, *_args) -> None:
        if self.hass is None:
            return
        self.hass.async_create_task(self.async_refresh_options())

    async def async_refresh_options(self, write_state: bool = True) -> None:
        entries = await self.manager.async_list()
        options = [UPLOAD_OPTION, TEST_PATTERN_OPTION, URL_DOWNLOAD_OPTION] + [str(item["id"]) for item in entries]
        if options != self._options:
            self._options = options
            if self._current and self._current not in options:
                self._current = None
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

        if option == TEST_PATTERN_OPTION:
            await self._async_push_test_pattern()
            return

        if option == URL_DOWNLOAD_OPTION:
            self._async_open_url_flow()
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
        """Open the upload form.

        This starts a *reconfigure* config flow, which is the documented way
        for an entity to ask the user for input:

            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_RECONFIGURE, "entry_id": entry_id},
            )

        Home Assistant reads the entry id from that context and routes the flow
        to `MojipicConfigFlow.async_step_reconfigure`, which shows the file
        picker.

        An earlier version used a repairs flow instead. That path does not
        work: `homeassistant.helpers.issue_registry` has no module-level
        `async_get_issue` (the method lives on the registry returned by
        `async_get`), and `IssueEntry` has no `async_create_flow` at all.
        Clicking the option raised AttributeError in the UI.
        """
        # `flow.async_init` is a coroutine, so it must be scheduled rather
        # than awaited -- `async_select_option` returns before the dialog is
        # built, which is what we want.
        self.hass.async_create_task(
            self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": config_entries.SOURCE_RECONFIGURE,
                    "entry_id": self.entry.entry_id,
                },
            )
        )
        # Uploading is a side action, not a selection, so put the dropdown back
        # to whatever it was showing.
        self.async_write_ha_state()

    async def _async_push_test_pattern(self) -> None:
        """Generate and display a static black/white checkerboard."""
        from PIL import Image
        from .image_encode import encode_framebuffer
        size = 32
        img = Image.new("RGB", (size, size), "black")
        pixels = img.load()
        for y in range(size):
            for x in range(size):
                if (x // 4 + y // 4) % 2 == 0:
                    pixels[x, y] = (255, 255, 255)
        # Store as a real image entry so the slot is valid and the image
        # appears in the dropdown. The manager assigns the next free id.
        import io
        raw = io.BytesIO()
        img.save(raw, format="PNG")
        stored = await self.manager.async_upload(raw.getvalue(), "test_pattern.png")
        framebuffer = await self.manager.async_framebuffer(stored["id"])
        result = await self.coordinator.async_display(
            framebuffer, stored["id"]
        )
        if result.outcome.ok:
            self._current = TEST_PATTERN_OPTION
            self.async_write_ha_state()
            await self.manager.async_save()

    async def _async_open_url_flow(self) -> None:
        """Open URL input dialog via reconfigure flow."""
        self.hass.async_create_task(
            self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": config_entries.SOURCE_RECONFIGURE,
                    "entry_id": self.entry.entry_id,
                    "url_mode": True,
                },
            )
        )
        # The dialog opens asynchronously; keep current option.
        self.async_write_ha_state()
