"""BLE transport abstraction for MojiPic.

Follows Home Assistant's documented pattern for Bluetooth integrations:

  * Discover through Home Assistant's Bluetooth *manager* -- check what it
    already knows, then subscribe to its advertisement stream. Calling
    ``discover()`` on the shared scanner does not work: HA already runs a
    scanner, so it returns immediately with nothing cached.
  * Connect with ``BleakClient`` over the ``BLEDevice`` that manager resolved,
    so the connection is routed back through whichever adapter or ESPHome
    Bluetooth proxy actually saw the device.
  * Use ``bleak_retry_connector.establish_connection`` and a connection
    timeout of at least 10 seconds: BlueZ must resolve services on first
    contact, and transient failures are normal.
  * Do not reuse a ``BleakClient`` across connections; a fresh one per
    operation is more reliable.

**Honest status.** This layer has never executed against a real Home Assistant
instance. It is written against the documented API and reviewed against the
Home Assistant source, but only a live host can confirm it.

Four API errors were found here after live attempts, all the same kind -- a
signature or behaviour assumed rather than read:

  * ``bluetooth.async_scanner_count(hass, connectable)`` takes ``hass``; it was
    being called with no arguments, raising ``TypeError`` and surfacing as
    "Unknown error occurred" when the scan form was submitted.
  * ``bluetooth.async_register_callback(hass, callback, match_dict, mode)`` has
    ``match_dict`` as its third positional argument; the scanning mode was being
    passed into that slot, silently disabling the subscription.
  * ``bluetooth.async_ble_device_from_address(hass, address)`` is synchronous and
    returns a ``BLEDevice``, not a connected client. It was being awaited and
    treated as a client with ``write_gatt_char``.
  * ``BleakScanner.discover()`` on HA's shared scanner returns immediately with
    whatever is cached -- almost always nothing -- because HA is already
    scanning. The form failed instantly with "No MojiPic found" without ever
    listening. Discovery must consume the manager's advertisement stream.
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
    def _bluetooth(self):
        from homeassistant.components import bluetooth

        return bluetooth

    # -- discovery ----------------------------------------------------------
    async def async_discover(
        self, name_prefix: str, timeout: int
    ) -> Optional[str]:
        """Find a MojiPic using Home Assistant's Bluetooth manager.

        Two things are wrong with simply calling `discover()` on the shared
        scanner, and both were found the hard way:

        1. Home Assistant already runs a scanner continuously. Asking a
           running bleak scanner to `discover()` returns immediately with
           whatever it happens to have cached -- usually nothing. The scan
           appears to be skipped and the setup form fails instantly with
           "No MojiPic found".

        2. `discover()` gives no access to advertisements that arrived before
           the call.

        So instead: check what Home Assistant already knows, then subscribe to
        its advertisement stream and wait for a match. This is the documented
        callback pattern and it works with whatever adapter or ESPHome proxy
        the user has.
        """
        prefix = (name_prefix or "").strip().lower()
        if not prefix:
            return None

        try:
            bluetooth = self._bluetooth()
        except ImportError as err:  # pragma: no cover
            raise TransportError(f"bluetooth is unavailable: {err}") from err

        found: dict[str, str] = {}
        loop = asyncio.get_running_loop()

        def _consider(address: Optional[str], *names: Optional[str]) -> None:
            if not address:
                return
            for name in names:
                if name and name.strip().lower().startswith(prefix):
                    found.setdefault(address.upper(), name.strip())
                    return

        # 1. Anything Home Assistant has already seen. A device that
        #    advertised before the form was submitted is here, and waiting
        #    again for it would waste the whole timeout.
        try:
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            ):
                _consider(
                    getattr(info, "address", None),
                    getattr(info, "name", None),
                    getattr(getattr(info, "advertisement", None), "local_name", None),
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not read known bluetooth services: %s", err)

        if found:
            address = next(iter(found))
            _LOGGER.debug("Discovered MojiPic (cached) at %s", address)
            self._address = address
            return address

        # 2. Subscribe to advertisements for the remainder of the window.
        #
        # The callback MUST accept two arguments. Home Assistant's type alias
        # is verbatim:
        #
        #     type BluetoothCallback = Callable[
        #         [BluetoothServiceInfoBleak, BluetoothChange], None
        #     ]
        #
        # and the manager invokes it as `callback(service_info,
        # BluetoothChange.ADVERTISEMENT)`. A one-argument callback raises on
        # the very first replayed advertisement:
        #
        #     Error in bluetooth callback
        #     TypeError: ..._on_service_info() takes 1 positional argument
        #                but 2 were given
        #
        # which Home Assistant logs from
        # components/bluetooth/manager.py:_async_replay_history_for_callback.
        # Discovery then never finds anything, and the error surfaces as
        # "could not discover the display" rather than as an arity problem.
        def _on_service_info(service_info, change=None) -> None:
            _consider(
                getattr(service_info, "address", None),
                getattr(service_info, "name", None),
                getattr(getattr(service_info, "advertisement", None), "local_name", None),
            )

        try:
            # match_dict and mode are positional-or-keyword, in that order.
            # Passing the mode third binds it to match_dict instead, which
            # silently disables the subscription.
            cancel = bluetooth.async_register_callback(
                self.hass,
                _on_service_info,
                None,
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        except Exception as err:  # noqa: BLE001
            raise TransportError(f"could not watch for advertisements: {err}") from err

        try:
            deadline = loop.time() + timeout
            while loop.time() < deadline and not found:
                await asyncio.sleep(0.25)
        finally:
            cancel()

        if not found:
            return None

        address = next(iter(found))
        _LOGGER.debug("Discovered MojiPic at %s (%s)", address, found[address])
        self._address = address
        return address

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
