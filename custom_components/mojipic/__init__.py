"""The MojiPic integration.

Provides image upload and display for a MojiPic 32x32 BLE display, plus a few
device controls. The point of this integration is event -> image, so the
services are the product; the entities are deliberately minimal.

MojiPic does not support Bluetooth pairing. The device address is *remembered*
in the config entry and reused, which is reported as "configured" rather than
"paired" so the wording stays accurate.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ADDRESS,
    ATTR_BRIGHTNESS,
    ATTR_FILENAME,
    ATTR_IMAGE_DATA,
    ATTR_IMAGE_ID,
    ATTR_IMAGE_PATH,
    DOMAIN,
    IMAGE_STORAGE_DIR,
)
from .coordinator import MojipicCoordinator
from .image_encode import ImageConversionError, encode_framebuffer, load_image
from .image_manager import ImageManager
from .services_def import (
    SERVICE_CLEAR_SCREEN,
    SERVICE_DELETE_IMAGE,
    SERVICE_DEVICE_OFF,
    SERVICE_DEVICE_ON,
    SERVICE_DISPLAY_IMAGE,
    SERVICE_DISPLAY_STORED,
    SERVICE_RESCAN,
    SERVICE_SCREEN_OFF,
    SERVICE_SCREEN_ON,
    SERVICE_SET_BRIGHTNESS,
    SERVICE_UPLOAD_IMAGE,
    SCHEMAS,
)
from .transport import TransportError, default_transport

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["image", "select", "sensor"]



async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services. Runs once, on the first config entry setup."""
    async def _handle(call: ServiceCall) -> None:
        await _async_dispatch(hass, call)

    for service, schema in SCHEMAS.items():
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, _handle, schema=schema)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MojiPic from a config entry."""
    transport = default_transport(hass)

    coordinator = MojipicCoordinator(
        hass, entry, transport,
        packets_for_framebuffer=_packets_for_framebuffer,
    )
    manager = ImageManager(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "manager": manager,
    }

    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if data:
            await data["coordinator"].async_stop()
    return unloaded


def _packets_for_framebuffer(framebuffer: bytes, image_id: int) -> list[bytes]:
    """Build BLE packets from a cached framebuffer.

    Importable at module level (no HA imports) so it can be unit tested offline.
    """
    from .image_encode import frame_packets, validate_slot
    from .const import HEADER_PREFIX_5BIT

    full_id = (HEADER_PREFIX_5BIT << 12) | validate_slot(image_id)
    return list(frame_packets(full_id, framebuffer))


# --- service dispatch ------------------------------------------------------
def _entries(hass: HomeAssistant, call: ServiceCall):
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No MojiPic is configured yet.")
    wanted = call.data.get(ATTR_ADDRESS)
    if not wanted:
        return entries
    wanted = wanted.upper()
    for entry in entries:
        if entry.data.get("address", "").upper() == wanted:
            return [entry]
    raise HomeAssistantError(f"No MojiPic configured with address {wanted}.")


def _data_for(hass: HomeAssistant, entry: ConfigEntry):
    return hass.data[DOMAIN][entry.entry_id]


async def _async_dispatch(hass: HomeAssistant, call: ServiceCall) -> None:
    service = call.service

    if service == SERVICE_UPLOAD_IMAGE:
        await _async_upload(hass, call)
        return

    for entry in _entries(hass, call):
        bundle = _data_for(hass, entry)
        coordinator: MojipicCoordinator = bundle["coordinator"]
        manager: ImageManager = bundle["manager"]

        if service == SERVICE_DELETE_IMAGE:
            removed = await manager.async_delete(call.data[ATTR_IMAGE_ID])
            if not removed:
                _LOGGER.debug(
                    "Delete requested for unknown MojiPic image %s",
                    call.data[ATTR_IMAGE_ID],
                )
        elif service == SERVICE_DISPLAY_STORED:
            await _async_display_stored(coordinator, manager,
                                        call.data[ATTR_IMAGE_ID])
        elif service == SERVICE_DISPLAY_IMAGE:
            await _async_display_file(hass, coordinator,
                                      call.data[ATTR_IMAGE_PATH])
        elif service == SERVICE_SET_BRIGHTNESS:
            await coordinator.async_set_brightness(call.data[ATTR_BRIGHTNESS])
        elif service == SERVICE_CLEAR_SCREEN:
            await coordinator.async_clear_screen()
        elif service == SERVICE_DEVICE_ON:
            await coordinator.async_device_on()
        elif service == SERVICE_DEVICE_OFF:
            await coordinator.async_device_off()
        elif service == SERVICE_SCREEN_ON:
            await coordinator.async_screen_on()
        elif service == SERVICE_SCREEN_OFF:
            await coordinator.async_screen_off()
        elif service == SERVICE_RESCAN:
            new_address = await coordinator.async_rebind_if_missing()
            if not new_address:
                raise HomeAssistantError(
                    "Could not find a MojiPic advertising under "
                    f"'{coordinator.name_prefix}'. Move or power on the display "
                    "and try again, or check it is not connected to another app."
                )


async def _async_upload(hass: HomeAssistant, call: ServiceCall) -> None:
    """Store an uploaded image for every configured display."""
    filename = call.data.get(ATTR_FILENAME) or "image.png"
    raw = await _async_read_upload(hass, call)

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No MojiPic is configured yet.")

    stored_id = None
    for entry in entries:
        manager = _data_for(hass, entry)["manager"]
        target_id = call.data.get(ATTR_IMAGE_ID)
        entry_data = await manager.async_upload(
            raw, filename, image_id=target_id
        )
        stored_id = entry_data["id"]

    _LOGGER.info(
        "Stored MojiPic image %s (%s)", stored_id, filename
    )


async def _async_read_upload(hass: HomeAssistant, call: ServiceCall) -> bytes:
    """Accept either inline base64 or a path inside the config directory."""
    inline = call.data.get(ATTR_IMAGE_DATA)
    if inline:
        import base64

        try:
            return base64.b64decode(inline, validate=True)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(
                "image_data was not valid base64"
            ) from err

    path = call.data.get(ATTR_IMAGE_PATH)
    if not path:
        raise HomeAssistantError(
            "Provide either image_data (base64) or image_path."
        )

    config_dir = str(Path(hass.config.config_dir).resolve())
    resolved = Path(hass.config.path(path.lstrip("/"))).resolve()
    # Refuse to read outside the config directory.
    if not str(resolved).startswith(config_dir):
        raise HomeAssistantError(
            "image_path must point inside the Home Assistant config directory."
        )
    try:
        return await hass.async_add_executor_job(
            resolved.read_bytes
        )
    except OSError as err:
        raise HomeAssistantError(f"Could not read that file: {err}") from err


async def _async_display_stored(
    coordinator: MojipicCoordinator, manager: ImageManager, image_id: int
) -> None:
    framebuffer = await manager.async_framebuffer(image_id)
    if framebuffer is None:
        available = [item["id"] for item in await manager.async_list()]
        raise HomeAssistantError(
            f"No stored MojiPic image with id {image_id}. "
            f"Available ids: {available or 'none yet'}"
        )
    result = await coordinator.async_display(framebuffer, image_id)
    _report(result)


async def _async_display_file(
    hass: HomeAssistant, coordinator: MojipicCoordinator, path: str
) -> None:
    config_dir = str(Path(hass.config.config_dir).resolve())
    resolved = Path(hass.config.path(path.lstrip("/"))).resolve()
    if not str(resolved).startswith(config_dir):
        raise HomeAssistantError(
            "image_path must point inside the Home Assistant config directory."
        )
    try:
        raw = await hass.async_add_executor_job(resolved.read_bytes)
    except OSError as err:
        raise HomeAssistantError(f"Could not read that file: {err}") from err

    try:
        framebuffer = encode_framebuffer(load_image(raw))
    except ImageConversionError as err:
        raise HomeAssistantError(str(err)) from err

    result = await coordinator.async_display(framebuffer, 0)
    _report(result)


def _report(result) -> None:
    """Log the real outcome, including the unverified distinction."""
    if result.outcome.ok:
        _LOGGER.info(
            "MojiPic image %s %s (%d packets, %.1fs) - %s",
            result.image_id, result.outcome.value, result.packets_sent,
            result.duration, result.verification,
        )
    elif result.outcome.value == "abandoned":
        _LOGGER.debug(
            "MojiPic push abandoned: %s", result.verification or result.error
        )
    else:
        _LOGGER.warning(
            "MojiPic push failed: %s", result.error or "unknown error"
        )
