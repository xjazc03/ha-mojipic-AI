"""BLE transport abstraction for MojiPic.

This is the seam that makes the integration testable without hardware, and
swappable if in-process writes turn out not to work.

**Honest status of the transport:** the default transport uses Home Assistant
Core's own Bluetooth stack for *active GATT connections and writes*. HA's
Bluetooth support is primarily built around passive advertisement monitoring,
so **whether it can sustain 192 write-with-response operations has not been
verified.** This was not testable on the development host, which has no
Bluetooth radio. Treat it as pending on-host verification.

If it turns out to be unsuitable, implement `MojipicTransport` and pass it in.
Nothing else needs to change.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
from typing import Any, Optional

from .const import (
    CHAR_BATTERY_LEVEL,
    CHAR_BRIGHTNESS,
    CHAR_DEVICE_COMMANDS,
    CHAR_FIRMWARE_VERSION,
    CHAR_MANUFACTURER,
    CHAR_MODEL,
    CHAR_PROCESSOR_ID,
    CHAR_SW_REVISION,
    CHAR_WRITE_IMAGE,
    CONNECT_TIMEOUT,
    RSSI_WARN_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)


class TransportError(Exception):
    """A BLE transport-level failure."""


class DeviceNotFound(TransportError):
    """No matching device could be discovered."""


class MojipicTransport(abc.ABC):
    """The interface the integration needs from a BLE link.

    Implement this to change how Home Assistant reaches the display.
    """

    @abc.abstractmethod
    async def async_discover(self, name_prefix: str, timeout: int) -> Optional[str]:
        """Scan for a device advertising `name_prefix`. Return its address."""

    @abc.abstractmethod
    async def async_connect(self, address: str) -> None:
        """Open a GATT connection."""

    @abc.abstractmethod
    async def async_disconnect(self) -> None:
        """Close the GATT connection."""

    @abc.abstractmethod
    async def async_write(self, characteristic: str, data: bytes, response: bool) -> None:
        """Write one characteristic value."""

    @abc.abstractmethod
    async def async_read(self, characteristic: str) -> Optional[bytes]:
        """Read one characteristic value, or None if unreadable."""

    async def async_rssi(self) -> Optional[int]:
        """Signal strength in dBm, if the transport can report it."""
        return None

    @property
    def connected(self) -> bool:
        return False


class HomeAssistantBluetoothTransport(MojipicTransport):
    """Default transport, using Home Assistant Core's Bluetooth stack.

    Pending on-host verification -- see the module docstring.
    """

    def __init__(self, hass) -> None:
        self.hass = hass
        self._client = None
        self._address: Optional[str] = None

    async def async_discover(
        self, name_prefix: str, timeout: int
    ) -> Optional[str]:
        from homeassistant.components import bluetooth

        found: dict[str, str] = {}
        loop = asyncio.get_running_loop()

        @bluetooth.callback
        def _on_advertisement(device, advertisement_data) -> None:
            name = (advertisement_data.local_name or device.name or "").strip()
            if name.lower().startswith(name_prefix.lower()):
                found.setdefault(device.address, name)

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
        _LOGGER.debug("Discovered MojiPic at %s", address)
        return address

    async def async_connect(self, address: str) -> None:
        from homeassistant.components import bluetooth

        await self.async_disconnect()
        self._address = address
        try:
            self._client = await asyncio.wait_for(
                bluetooth.async_ble_device_from_address(self.hass, address, True),
                timeout=CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            raise TransportError(
                f"timed out connecting to {address}"
            ) from err
        except Exception as err:  # noqa: BLE001
            raise TransportError(f"could not connect to {address}: {err}") from err

        if self._client is None:
            raise TransportError(
                f"{address} is not reachable. Check the display is powered on, "
                "in range, and not connected to another app."
            )

        rssi = await self.async_rssi()
        if rssi is not None:
            if rssi < RSSI_WARN_THRESHOLD:
                _LOGGER.warning(
                    "MojiPic link is weak (rssi=%s dBm). Pushes may fail "
                    "intermittently; consider moving the Bluetooth adapter "
                    "closer to the display or using an ESPHome Bluetooth proxy.",
                    rssi,
                )
            else:
                _LOGGER.debug("MojiPic connected, rssi=%s dBm", rssi)

    async def async_disconnect(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
        self._client = None

    async def async_write(
        self, characteristic: str, data: bytes, response: bool
    ) -> None:
        if self._client is None:
            raise TransportError("not connected")
        try:
            await self._client.write_gatt_char(characteristic, data, response=response)
        except Exception as err:  # noqa: BLE001
            raise TransportError(f"write to {characteristic} failed: {err}") from err

    async def async_read(self, characteristic: str) -> Optional[bytes]:
        if self._client is None:
            return None
        try:
            data = await self._client.read_gatt_char(characteristic)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("read %s failed: %s", characteristic, err)
            return None
        return bytes(data) if data else None

    async def async_rssi(self) -> Optional[int]:
        if self._client is None:
            return None
        try:
            # BleakClient exposes get_services(); rssi is available on some
            # backends via the raw handle. Not portable, so treat as best-effort.
            rssi = getattr(self._client, "rssi", None)
            if callable(rssi):
                rssi = rssi()
            if rssi is None:
                return None
            return int(rssi)
        except Exception:  # noqa: BLE001
            return None

    @property
    def connected(self) -> bool:
        if self._client is None:
            return False
        checker = getattr(self._client, "is_connected", None)
        return bool(checker) if callable(checker) else bool(checker)


# --- control payloads ------------------------------------------------------
# Transcribed from the vendor app's BluetoothDeviceUtilKt.
COMMAND_DEVICE_ON = b"\x02\x01"
COMMAND_DEVICE_OFF = b"\x02\x02"
COMMAND_SCREEN_ON = b"\x02\x06\x05"
COMMAND_SCREEN_OFF = b"\x02\x05"
COMMAND_CLEAR_SCREEN = b"\x07\x01"


def default_transport(hass) -> MojipicTransport:
    """Factory so tests can substitute a transport without patching internals."""
    return HomeAssistantBluetoothTransport(hass)
