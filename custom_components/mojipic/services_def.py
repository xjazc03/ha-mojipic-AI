"""Service definitions for the MojiPic integration."""

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ADDRESS,
    ATTR_BRIGHTNESS,
    ATTR_FILENAME,
    ATTR_IMAGE_DATA,
    ATTR_IMAGE_ID,
    ATTR_IMAGE_PATH,
    ATTR_SLOT,
)

SERVICE_UPLOAD_IMAGE = "upload_image"
SERVICE_DELETE_IMAGE = "delete_image"
SERVICE_DISPLAY_IMAGE = "display_image"
SERVICE_DISPLAY_STORED = "display_stored"
SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_CLEAR_SCREEN = "clear_screen"
SERVICE_DEVICE_ON = "device_on"
SERVICE_DEVICE_OFF = "device_off"
SERVICE_SCREEN_ON = "screen_on"
SERVICE_SCREEN_OFF = "screen_off"
SERVICE_RESCAN = "rescan"

_UPLOAD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_IMAGE_DATA): cv.string,
        vol.Optional(ATTR_IMAGE_PATH): cv.string,
        vol.Required(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_IMAGE_ID): vol.Coerce(int),
    }
)

_DELETE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IMAGE_ID): vol.Coerce(int),
        vol.Optional(ATTR_ADDRESS): cv.string,
    }
)

_DISPLAY_STORED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IMAGE_ID): vol.Coerce(int),
        vol.Optional(ATTR_ADDRESS): cv.string,
    }
)

_DISPLAY_FILE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IMAGE_PATH): cv.string,
        vol.Optional(ATTR_ADDRESS): cv.string,
    }
)

_BRIGHTNESS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_BRIGHTNESS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(ATTR_ADDRESS): cv.string,
    }
)

_ADDRESS_ONLY = vol.Schema({vol.Optional(ATTR_ADDRESS): cv.string})

SCHEMAS = {
    SERVICE_UPLOAD_IMAGE: _UPLOAD_SCHEMA,
    SERVICE_DELETE_IMAGE: _DELETE_SCHEMA,
    SERVICE_DISPLAY_STORED: _DISPLAY_STORED_SCHEMA,
    SERVICE_DISPLAY_IMAGE: _DISPLAY_FILE_SCHEMA,
    SERVICE_SET_BRIGHTNESS: _BRIGHTNESS_SCHEMA,
    SERVICE_CLEAR_SCREEN: _ADDRESS_ONLY,
    SERVICE_DEVICE_ON: _ADDRESS_ONLY,
    SERVICE_DEVICE_OFF: _ADDRESS_ONLY,
    SERVICE_SCREEN_ON: _ADDRESS_ONLY,
    SERVICE_SCREEN_OFF: _ADDRESS_ONLY,
    SERVICE_RESCAN: _ADDRESS_ONLY,
}
