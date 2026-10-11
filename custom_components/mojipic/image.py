"""Image entity: shows the image currently on the display."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_NAME, DEFAULT_NAME_PREFIX, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MojipicImage(hass, entry, bundle)])


class MojipicImage(ImageEntity):
    """The most recently displayed image, mirrored into Home Assistant."""

    _attr_should_poll = False
    _attr_translation_key = "image"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.coordinator = bundle["coordinator"]
        self.manager = bundle["manager"]
        address = entry.data["address"]
        self._attr_unique_id = f"{address.lower()}_image"
        self._attr_name = f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} image"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            # manufacturer/model are filled in once the device answers
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )
        self._cached: bytes | None = None
        self._content_type: str | None = None
        self._last_image_id: int | None = None
        self._last_updated: str | None = None

        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_state",
            self._handle_coordinator_update,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # `hass` is assigned by the entity platform after construction. A
        # dispatcher signal can arrive before that, and calling
        # async_write_ha_state then raises
        # "AttributeError: Attribute hass is None for <entity unknown>".
        # That error repeats on every signal and is pure noise.
        if self.hass is None:
            return
        manufacturer = self.coordinator.manufacturer
        model = self.coordinator.firmware
        if manufacturer or model:
            self._attr_device_info.update(
                manufacturer=manufacturer or None,
                model=f"32x32 BLE display (firmware {model})" if model else None,
            )
        self.async_write_ha_state()

    @callback
    def async_set_image(self, raw: bytes, content_type: str, image_id: int) -> None:
        """Called after a successful push."""
        self._cached = raw
        self._content_type = content_type
        self._last_image_id = image_id
        self._last_updated = dt_util.utcnow().isoformat()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._cached is not None

    async def async_image(self) -> bytes | None:
        return self._cached

    @property
    def content_type(self) -> str:
        return self._content_type or "image/png"

    @property
    def extra_state_attributes(self):
        attrs = {}
        if self._last_image_id is not None:
            attrs["image_id"] = self._last_image_id
        if self._last_updated:
            attrs["last_updated"] = self._last_updated
        # "configured", not "paired": MojiPic has no Bluetooth pairing.
        attrs["connection_state"] = self.coordinator.link_state
        return attrs
