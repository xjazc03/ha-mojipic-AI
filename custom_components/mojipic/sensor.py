"""Battery sensor for MojiPic.

Note: MojiPic is a 12V automotive accessory. A reading here usually means it is
charging, or running on its internal 7500 mAh pack.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME, DEFAULT_NAME_PREFIX, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MojipicBattery(hass, entry, bundle)])


class MojipicBattery(SensorEntity):
    """Battery percentage read over BLE."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = "measurement"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bundle) -> None:
        self.coordinator = bundle["coordinator"]
        address = entry.data["address"]
        self._attr_unique_id = f"{address.lower()}_battery"
        self._attr_name = (
            f"{entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX} battery"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX,
            connections={(CONNECTION_BLUETOOTH, address.upper())},
        )
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_state",
            self._handle_update,
        )

    def _handle_update(self) -> None:
        # Guard: see image.py. Writing state before the entity platform has
        # assigned `hass` raises and repeats on every signal.
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self.coordinator.battery

    @property
    def available(self) -> bool:
        return self.coordinator.battery is not None
