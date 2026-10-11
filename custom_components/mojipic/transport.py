"""BLE transport abstraction for MojiPic.

Follows Home Assistant's documented pattern for Bluetooth integrations:

  * Discover with ``bluetooth.async_get_scanner(hass)``, which returns the
    shared ``BleakScanner`` wrapper. This avoids starting a second scanner and
    keeps working when the user changes adapter settings.
  * Connect with ``BleakClient`` over the ``BLEDevice`` that scanner returned,
    so the connection is routed back through whichever adapter or ESPHome
    Bluetooth proxy actually saw the device.
  * Use a connection timeout of at least 10 seconds: BlueZ must resolve
    services on first contact, and transient failures are normal.
  * Do not reuse a ``BleakClient`` across connections; a fresh one per
    operation is more reliable.

**Honest status.** This layer has never executed against a real Home Assistant
instance. It is written against the documented API and reviewed against the
Home Assistant source, but only a live host can confirm it.

Three API errors were found here after the first live attempt, all of the same
kind -- a signature assumed rather than read:

  * ``bluetooth.async_scanner_count(hass, connectable)`` takes ``hass``; it was
    being called with no arguments, raising ``TypeError`` and surfacing as
    "Unknown error occurred" when the scan form was submitted.
  * ``bluetooth.async_register_callback(hass, callback, match_dict, mode)`` has
    ``match_dict`` as its third positional argument; the scanning mode was being
    passed into that slot.
  * ``bluetooth.async_ble_device_from_address(hass, address)`` is synchronous and
    returns a ``BLEDevice``, not a connected client. It was being awaited and
    treated as a client with ``write_gatt_char``.
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
    CHAR_PROCESSOR_ID,
    CHAR_SW_REVISION,
    CHAR_WRITE_IMAGE,
    CONNECT_TIMEOUT,
    RSSI_WARN_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)

# Home Assistant guidance: BlueZ needs to resolve services on first contact, so
# the connection timeout must be generous.
MIN_CONNECT_TIMEOUT = 10.0


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
    """Default transport, built on Home Assistant's Bluetooth stack."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self._client = None
        self._address: Optional[str] = None
        self._ble_device = None

    # -- helpers ------------------------------------------------------------
    def _scanner(self):
        """The shared Home Assistant BleakScanner wrapper."""
        from homeassistant.components import bluetooth

        return bluetooth.async_get_scanner(self.hass)

    def _bluetooth(self):
        from homeassistant.components import bluetooth

        return bluetooth

    @staticmethod
    def _advertised_name(device, advertisement_data) -> str:
        return (
            advertisement_data.local_name
            or getattr(device, "name", None)
            or ""
        ).strip()

    # -- discovery ----------------------------------------------------------
    async def async_discover(
        self, name_prefix: str, timeout: int
    ) -> Optional[str]:
        """Scan using Home Assistant's shared scanner."""
        prefix = (name_prefix or "").strip().lower()

        try:
            scanner = self._scanner()
        except Exception as err:  # noqa: BLE001
            raise TransportError(f"bluetooth is unavailable: {err}") from err

        try:
            devices = await scanner.discover(timeout=timeout, return_adv=True)
        except Exception as err:  # noqa: BLE001
            raise TransportError(f"bluetooth scan failed: {err}") from err

        for address, (device, advertisement_data) in (devices or {}).items():
            if self._advertised_name(device, advertisement_data).lower().startswith(prefix):
                _LOGGER.debug("Discovered MojiPic at %s", address)
                self._address = address.upper()
                return address.upper()

        return None

    # -- connection ---------------------------------------------------------
    async def async_connect(self, address: str) -> None:
        from bleak import BleakClient

        await self.async_disconnect()
        self._address = address

        # Prefer a BLEDevice the scanner has already seen: connecting through it
        # routes the connection to the adapter or proxy that actually has the
        # device in range. Fall back to the raw address if it is not known.
        ble_device = None
        try:
            bluetooth = self._bluetooth()
            # NOTE: this API is synchronous and returns a BLEDevice, not a client.
            if hasattr(bluetooth, "async_ble_device_from_address"):
                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass, address, True
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not resolve BLEDevice for %s: %s", address, err)

        timeout = max(MIN_CONNECT_TIMEOUT, CONNECT_TIMEOUT)

        # Home Assistant logs a warning if BleakClient.connect() is used
        # directly: "called without bleak-retry-connector. For reliable
        # connection establishment, use establish_connection()".
        #
        # A BLE link to a small battery-backed display is exactly the case
        # that needs retries: BlueZ must resolve services on first contact and
        # transient failures are common.
        client = BleakClient(ble_device or address, timeout=timeout)
        try:
            try:
                from bleak_retry_connector import establish_connection

                client = await establish_connection(
                    BleakClient,
                    ble_device if ble_device is not None else address,
                    self._address or address,
                    timeout=timeout,
                )
            except ImportError:
                # bleak-retry-connector is a Home Assistant dependency, but
                # fall back rather than fail outright if it is unavailable.
                _LOGGER.debug(
                    "bleak_retry_connector unavailable; using direct connect"
                )
                await client.connect()
        except Exception as err:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise TransportError(
                f"could not connect to {address}: {err}. Check the display is "
                "powered on, in range, and not connected to another app."
            ) from err

        if not client.is_connected:
            raise TransportError(f"{address} did not accept a connection")

        self._client = client
        self._ble_device = ble_device
        rssi = await self.async_rssi()
        if rssi is not None and rssi < RSSI_WARN_THRESHOLD:
            _LOGGER.warning(
                "MojiPic link is weak (rssi=%s dBm). Pushes may fail "
                "intermittently; consider moving the adapter or adding an "
                "ESPHome Bluetooth proxy closer to the display.",
                rssi,
            )

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
            await self._client.write_gatt_char(
                characteristic, data, response=response
            )
        except Exception as err:  # noqa: BLE001
            raise TransportError(
                f"write to {characteristic} failed: {err}"
            ) from err

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
        rssi = getattr(self._client, "rssi", None)
        if callable(rssi):
            with contextlib.suppress(Exception):
                rssi = rssi()
        try:
            return int(rssi) if rssi is not None else None
        except (TypeError, ValueError):
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
