"""MojiPic BLE transport built on Home Assistant's own bluetooth integration.

Using HA's `bluetooth` component rather than raw bleak is deliberate: it means
this works over *any* adapter HA can see -- a local Bluetooth adapter, an
ESPHome Bluetooth proxy, or the built-in one -- without needing direct USB
access from the HA container.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CHAR_BATTERY_LEVEL,
    CHAR_BRIGHTNESS,
    CHAR_DEVICE_COMMANDS,
    CHAR_FIRMWARE_VERSION,
    CHAR_IMAGE_ID_TO_DISPLAY,
    CHAR_PROCESSOR_ID,
    CHAR_WRITE_IMAGE,
    DEFAULT_SCAN_TIMEOUT,
    DEVICE_NAME_PREFIX,
    UPLOAD_TIMEOUT,
)
from .protocol import packets_for_image

_LOGGER = logging.getLogger(__name__)


class MojipicApiError(HomeAssistantError):
    """A BLE-level failure talking to a MojiPic."""


class MojipicApi:
    """Async client for a single MojiPic display."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str | None = None,
        slot: int = 0,
    ) -> None:
        self.hass = hass
        self.address = address.upper()
        self.name = name
        self.slot = slot

    # -- discovery ----------------------------------------------------------
    async def async_find_device(self, timeout: int = DEFAULT_SCAN_TIMEOUT) -> str | None:
        """Scan for a MojiPic by advertised name and return its BLE address.

        Home Assistant already scans continuously when the `bluetooth`
        integration is set up, so this registers a callback and simply waits for
        a matching advertisement to arrive. Returns None on timeout.
        """
        import asyncio

        found: dict[str, str] = {}
        loop = asyncio.get_running_loop()

        @callback
        def _on_advertisement(device, advertisement_data) -> None:
            adv_name = (advertisement_data.local_name or device.name or "").strip()
            if adv_name.lower().startswith(DEVICE_NAME_PREFIX):
                found.setdefault(device.address, adv_name)

        cancel = bluetooth.async_register_callback(
            self.hass, _on_advertisement, bluetooth.BluetoothScanningMode.ACTIVE
        )
        try:
            deadline = loop.time() + timeout
            while loop.time() < deadline and not found:
                await asyncio.sleep(0.5)
        finally:
            cancel()

        if not found:
            return None
        address = next(iter(found))
        self.name = self.name or found[address]
        return address

    # -- connection ---------------------------------------------------------
    async def async_get_client(self, connect: bool = True):
        """Return an HA bluetooth client for this device."""
        try:
            client = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connect
            )
        except Exception as err:  # noqa: BLE001 - surface as HA error
            raise MojipicApiError(
                f"could not reach MojiPic at {self.address}: {err}"
            ) from err
        if client is None:
            raise MojipicApiError(
                f"MojiPic {self.address} is not reachable. Check it is powered on, "
                "in range, and not connected to the vendor app."
            )
        return client

    # -- operations ---------------------------------------------------------
    async def async_display_image(
        self,
        image,
        slot: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        """Push one image to the panel.

        Real hardware advertises FFF1 as `write`, so every one of the 192 packets
        is a write-with-response. There is no flash-ready handshake on this
        firmware (FFF2 only ever emits 0x00), so completion is implied by the
        writes returning.
        """
        target_slot = self.slot if slot is None else slot
        packets = packets_for_image(image, target_slot)

        client = await self.async_get_client(connect=True)
        try:
            total = len(packets)
            for index, packet in enumerate(packets):
                try:
                    await client.write_gatt_char(
                        CHAR_WRITE_IMAGE, packet, response=True
                    )
                except Exception as err:  # noqa: BLE001
                    raise MojipicApiError(
                        f"upload failed at packet {index}/{total}: {err}"
                    ) from err
                if progress is not None and index % 16 == 0:
                    progress(min(100, int(index * 100 / total)))
            if progress is not None:
                progress(100)
        finally:
            await self._async_disconnect(client)

    async def async_set_brightness(self, level: int) -> None:
        """Set panel brightness, 0-100. Uses write-without-response (FFF9)."""
        level = max(0, min(100, int(level)))
        client = await self.async_get_client(connect=True)
        try:
            await client.write_gatt_char(
                CHAR_BRIGHTNESS, bytes([level]), response=False
            )
        finally:
            await self._async_disconnect(client)

    async def async_send_command(self, payload: bytes) -> None:
        """Write a raw control payload to FFF6 (device commands)."""
        client = await self.async_get_client(connect=True)
        try:
            await client.write_gatt_char(CHAR_DEVICE_COMMANDS, payload, response=True)
        finally:
            await self._async_disconnect(client)

    async def async_device_on(self) -> None:
        await self.async_send_command(b"\x02\x01")

    async def async_device_off(self) -> None:
        await self.async_send_command(b"\x02\x02")

    async def async_screen_on(self) -> None:
        await self.async_send_command(b"\x02\x06\x05")

    async def async_screen_off(self) -> None:
        await self.async_send_command(b"\x02\x05")

    async def async_clear_screen(self) -> None:
        await self.async_send_command(b"\x07\x01")

    async def async_read_info(self) -> dict[str, Any]:
        """Read the readable characteristics that report back real values."""
        client = await self.async_get_client(connect=True)
        info: dict[str, Any] = {}
        try:
            info["battery"] = await self._async_read(client, CHAR_BATTERY_LEVEL)
            info["firmware"] = await self._async_read_str(client, CHAR_FIRMWARE_VERSION)
            info["processor_id"] = await self._async_read_str(
                client, CHAR_PROCESSOR_ID
            )
        finally:
            await self._async_disconnect(client)
        return info

    @staticmethod
    async def _async_read(client, uuid: str) -> int | None:
        try:
            data = await client.read_gatt_char(uuid)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("read %s failed: %s", uuid, err)
            return None
        if not data:
            return None
        return data[0] & 0xFF

    @staticmethod
    async def _async_read_str(client, uuid: str) -> str | None:
        try:
            data = await client.read_gatt_char(uuid)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("read %s failed: %s", uuid, err)
            return None
        if not data:
            return None
        return bytes(data).decode("utf-8", "replace").strip("\x00")

    @staticmethod
    async def _async_disconnect(client) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
