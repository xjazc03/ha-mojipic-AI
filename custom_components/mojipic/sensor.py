"""Battery sensor for MojiPic."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import MojipicApi, MojipicApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = dt_util.timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    api = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = MojipicBatteryCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([MojipicBattery(coordinator, entry)])


class MojipicBatteryCoordinator(DataUpdateCoordinator):
    """Polls the device for battery level."""

    def __init__(self, hass: HomeAssistant, api: MojipicApi, entry: ConfigEntry) -> None:
        self.api = api
        super().__init__(
            hass,
            _LOGGER,
            name=f"{entry.title} battery",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> int | None:
        try:
            info = await self.api.async_read_info()
        except MojipicApiError as err:
            raise UpdateFailed(f"could not read MojiPic: {err}") from err
        return info.get("battery")


class MojipicBattery(SensorEntity):
    """Battery percentage as reported over BLE."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = "measurement"

    def __init__(self, coordinator: MojipicBatteryCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.data['address'].lower()}_battery"
        self._attr_name = f"{entry.data.get('name') or 'Mojipic'} battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"].lower())},
            name=entry.data.get("name") or "Mojipic",
            manufacturer="Infinitum Trading (IIP)",
            model="MojiPic 32x32 RGB LED matrix",
            connections={(CONNECTION_BLUETOOTH, entry.data["address"].upper())},
        )

    @property
    def native_value(self) -> int | None:
        return self._coordinator.data

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and self._coordinator.data is not None
