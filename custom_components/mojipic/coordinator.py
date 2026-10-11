"""Connection lifecycle and push scheduling for MojiPic.

Behaviour that exists because the link is unreliable in practice:

  * persistent connection with capped exponential backoff that never gives up
  * latest-request-wins push queue: a superseded transfer is abandoned rather
    than queued behind, because a stale image on the wall is worse than a
    slightly slower correct one
  * **honest reporting.** This firmware exposes no flash-ready flag and no
    trustworthy slot state, so a completed push cannot actually be confirmed.
    The outcome says `sent_unverified` rather than claiming a verification the
    hardware cannot support.

"Remembered", not "paired": MojiPic does not support Bluetooth pairing or
bonding. The integration remembers the device address and reconnects on that
basis. A dead link and a forgotten address are tracked as different states.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CHAR_BATTERY_LEVEL,
    CHAR_BRIGHTNESS,
    CHAR_DEVICE_COMMANDS,
    CHAR_FIRMWARE_VERSION,
    CHAR_MANUFACTURER,
    CHAR_PROCESSOR_ID,
    CHAR_SW_REVISION,
    CHAR_WRITE_IMAGE,
    CONF_NAME,
    DEFAULT_NAME_PREFIX,
    DOMAIN,
    HEALTH_INTERVAL,
    PUSH_TIMEOUT,
    RECONNECT_BACKOFF_INITIAL,
    RECONNECT_BACKOFF_MAX,
    RSSI_WARN_THRESHOLD,
    STATE_CONFIGURED,
    STATE_UNREACHABLE,
)
from .transport import (
    COMMAND_CLEAR_SCREEN,
    COMMAND_DEVICE_OFF,
    COMMAND_DEVICE_ON,
    COMMAND_SCREEN_OFF,
    COMMAND_SCREEN_ON,
    MojipicTransport,
    TransportError,
)

_LOGGER = logging.getLogger(__name__)


class PushOutcome(enum.Enum):
    """Result of a push.

    Deliberately distinguishes sent from verified. Do not collapse this into a
    boolean -- the distinction is the point.
    """

    SENT_VERIFIED = "sent_verified"
    SENT_UNVERIFIED = "sent_unverified"
    ABANDONED = "abandoned"
    FAILED = "failed"

    @property
    def ok(self) -> bool:
        return self in (PushOutcome.SENT_VERIFIED, PushOutcome.SENT_UNVERIFIED)


@dataclass
class PushResult:
    """Outcome of one push, surfaced to the service caller and the log."""

    outcome: PushOutcome
    image_id: Optional[int] = None
    packets_sent: int = 0
    duration: float = 0.0
    rssi: Optional[int] = None
    error: Optional[str] = None
    verification: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "image_id": self.image_id,
            "packets_sent": self.packets_sent,
            "duration": round(self.duration, 3),
            "rssi": self.rssi,
            "error": self.error,
            "verification": self.verification,
        }


@dataclass
class _Pending:
    packets: list[bytes]
    image_id: Optional[int]
    future: Optional[asyncio.Future] = None


class MojipicCoordinator:
    """Owns the connection, the push queue and the device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        transport: MojipicTransport,
        packets_for_framebuffer: Callable[[bytes, int], list[bytes]],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.transport = transport
        self._packets_for = packets_for_framebuffer

        self.address: str = entry.data.get("address", "").upper()
        self.name: str = entry.data.get(CONF_NAME) or DEFAULT_NAME_PREFIX
        self.name_prefix: str = entry.data.get("name_prefix", DEFAULT_NAME_PREFIX)

        self._connected = False
        self._in_flight: Optional[_Pending] = None
        self._worker: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._unsub_interval = None
        self._running = False

        self.last_rssi: Optional[int] = None
        self.reconnect_attempts = 0
        self.stats = {"sent": 0, "failed": 0, "abandoned": 0, "reconnects": 0}
        self.battery: Optional[int] = None
        self.firmware: Optional[str] = None
        self.processor_id: Optional[str] = None
        self.manufacturer: Optional[str] = None
        self.software_revision: Optional[str] = None

        # "configured" means the address is remembered. It does NOT mean the
        # device is connected, and it is NOT Bluetooth pairing.
        self.link_state: str = STATE_UNREACHABLE

    # -- lifecycle ----------------------------------------------------------
    async def async_start(self) -> None:
        self._running = True
        self._ensure_worker()
        await self._async_ensure_connected()
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_health_tick,
            # async_track_time_interval requires a datetime.timedelta, not a
            # number of seconds. Passing a float raises
            # "AttributeError: 'float' object has no attribute 'total_seconds'"
            # and takes down the whole config entry setup.
            dt_util.timedelta(seconds=HEALTH_INTERVAL),
        )

    async def async_stop(self) -> None:
        self._running = False
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        for task in (self._worker, self._reconnect_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._worker = self._reconnect_task = None
        with contextlib.suppress(Exception):
            await self.transport.async_disconnect()
        self._connected = False
        self.link_state = STATE_UNREACHABLE

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = self.hass.async_create_task(self._async_worker())

    # -- connection ---------------------------------------------------------
    async def _async_ensure_connected(self) -> bool:
        if self._connected and self.transport.connected:
            return True
        if not self.address:
            self.link_state = STATE_UNREACHABLE
            return False
        try:
            await self.transport.async_connect(self.address)
        except TransportError as err:
            _LOGGER.debug("MojiPic connect failed: %s", err)
            self._connected = False
            self.link_state = STATE_UNREACHABLE
            return False

        self._connected = True
        self.link_state = STATE_CONFIGURED
        if self.reconnect_attempts:
            _LOGGER.info(
                "Reconnected to MojiPic after %d attempt(s)",
                self.reconnect_attempts,
            )
            self.stats["reconnects"] += 1
        self.reconnect_attempts = 0
        self.last_rssi = await self.transport.async_rssi()
        if self.last_rssi is not None and self.last_rssi < RSSI_WARN_THRESHOLD:
            _LOGGER.warning(
                "MojiPic link is weak (rssi=%s dBm). Pushes may fail "
                "intermittently.", self.last_rssi,
            )
        await self._async_refresh_device_info()
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry.entry_id}_state")
        return True

    async def _async_reconnect_loop(self) -> None:
        """Capped exponential backoff. Retries indefinitely; never gives up."""
        delay = RECONNECT_BACKOFF_INITIAL
        while self._running and not self._connected:
            self.reconnect_attempts += 1
            if await self._async_ensure_connected():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_BACKOFF_MAX)

    def _schedule_reconnect(self) -> None:
        if not self._running:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(self._async_reconnect_loop())

    async def _async_health_tick(self, _now=None) -> None:
        """Slow liveness check plus a rescan if the address has gone stale."""
        if not self._running:
            return
        if not self._connected or self._in_flight is not None:
            return
        if await self._async_ensure_connected():
            return
        self._schedule_reconnect()

    async def _async_refresh_device_info(self) -> None:
        async def read_byte(uuid):
            data = await self.transport.async_read(uuid)
            return data[0] & 0xFF if data else None

        async def read_str(uuid):
            data = await self.transport.async_read(uuid)
            return data.decode("utf-8", "replace").strip("\x00") if data else None

        battery, firmware, processor, manufacturer, sw = await asyncio.gather(
            read_byte(CHAR_BATTERY_LEVEL),
            read_str(CHAR_FIRMWARE_VERSION),
            read_str(CHAR_PROCESSOR_ID),
            read_str(CHAR_MANUFACTURER),
            read_str(CHAR_SW_REVISION),
        )
        self.battery = battery
        self.firmware = firmware
        self.processor_id = processor
        self.manufacturer = manufacturer
        self.software_revision = sw

    async def async_rebind_if_missing(self) -> Optional[str]:
        """Handle a stored address that stops responding.

        MojiPic does not support bonding, so a device can present a different
        address after a firmware update or battery service event. Attempt exactly
        one rescan and re-persist. Never silently forget an address.
        """
        _LOGGER.warning(
            "Remembered MojiPic address %s is not responding; "
            "attempting a single rescan", self.address,
        )
        new_address = await self.transport.async_discover(
            self.name_prefix, timeout=20
        )
        if not new_address or new_address.upper() == self.address:
            _LOGGER.warning("Rescan did not find a different MojiPic address")
            return None

        old = self.address
        self.address = new_address.upper()
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, "address": self.address},
        )
        _LOGGER.warning(
            "Re-bound MojiPic address: %s -> %s (persisted to the config entry)",
            old, self.address,
        )
        if await self._async_ensure_connected():
            async_dispatcher_send(
                self.hass, f"{DOMAIN}_{self.entry.entry_id}_rebound"
            )
            return self.address
        return None

    # -- push ---------------------------------------------------------------
    async def async_display(
        self, framebuffer: bytes, image_id: int = 0
    ) -> PushResult:
        """Queue an image for display. Latest request wins."""
        if not self._running:
            await self.async_start()
        else:
            self._ensure_worker()

        packets = self._packets_for(framebuffer, image_id)
        pending = _Pending(packets=packets, image_id=image_id)
        pending.future = self.hass.loop.create_future()

        previous = self._in_flight
        self._in_flight = pending

        if previous is not None and previous.future and not previous.future.done():
            _LOGGER.debug(
                "Superseding in-flight push (image %s -> %s)",
                previous.image_id, image_id,
            )
            previous.future.set_result(
                PushResult(PushOutcome.ABANDONED, image_id=previous.image_id,
                           verification="superseded by a newer image")
            )

        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.future), timeout=PUSH_TIMEOUT + 15
            )
        except asyncio.TimeoutError:
            if pending.future and not pending.future.done():
                result = PushResult(
                    PushOutcome.FAILED, image_id=image_id,
                    error=f"push did not complete within {PUSH_TIMEOUT + 15}s",
                    verification="timed out waiting for transfer",
                )
                pending.future.set_result(result)
                self._count(result)
                return result
            return pending.future.result()

    def _count(self, result: PushResult) -> None:
        """Tally an outcome. Single source of truth for the counters."""
        if result.outcome is PushOutcome.FAILED:
            self.stats["failed"] += 1
        elif result.outcome is PushOutcome.ABANDONED:
            self.stats["abandoned"] += 1
        elif result.outcome.ok:
            self.stats["sent"] += 1

    async def _async_worker(self) -> None:
        """Single-in-flight worker: never more than one transfer at a time."""
        while self._running:
            current = self._in_flight
            if current is None:
                await asyncio.sleep(0.02)
                continue
            if current.future and current.future.done():
                if self._in_flight is current:
                    self._in_flight = None
                self.stats["abandoned"] += 1
                continue
            try:
                result = await self._async_do_push(current)
            except Exception as err:  # noqa: BLE001
                result = PushResult(PushOutcome.FAILED,
                                    image_id=current.image_id, error=str(err))
            self._count(result)
            if current.future and not current.future.done():
                current.future.set_result(result)
            if self._in_flight is current:
                self._in_flight = None

    async def _async_do_push(self, pending: _Pending) -> PushResult:
        started = time.monotonic()

        if not await self._async_ensure_connected():
            self._schedule_reconnect()
            return PushResult(
                PushOutcome.FAILED, image_id=pending.image_id,
                error="the MojiPic is not reachable right now",
                verification="not attempted",
            )

        sent = 0
        total = len(pending.packets)
        try:
            for index, packet in enumerate(pending.packets):
                # A newer request replaces self._in_flight; that identity change
                # is what makes this transfer stale.
                if self._in_flight is not pending:
                    return PushResult(
                        PushOutcome.ABANDONED, image_id=pending.image_id,
                        packets_sent=sent,
                        duration=time.monotonic() - started,
                        verification="superseded by a newer image",
                    )
                await asyncio.wait_for(
                    self.transport.async_write(CHAR_WRITE_IMAGE, packet, True),
                    timeout=PUSH_TIMEOUT,
                )
                sent += 1
                if index % 32 == 0:
                    # Yield so supersede events and disconnects can be observed.
                    await asyncio.sleep(0)
        except asyncio.TimeoutError:
            self._connected = False
            self._schedule_reconnect()
            return PushResult(
                PushOutcome.FAILED, image_id=pending.image_id, packets_sent=sent,
                duration=time.monotonic() - started, error="write timed out",
                verification="transfer incomplete",
            )
        except TransportError as err:
            self._connected = False
            self._schedule_reconnect()
            self.last_rssi = await self.transport.async_rssi()
            _LOGGER.warning(
                "Push failed at packet %d/%d: %s (rssi=%s)",
                sent, total, err, self.last_rssi,
            )
            return PushResult(
                PushOutcome.FAILED, image_id=pending.image_id, packets_sent=sent,
                duration=time.monotonic() - started, rssi=self.last_rssi,
                error=str(err), verification="transfer incomplete",
            )

        return PushResult(
            PushOutcome.SENT_UNVERIFIED, image_id=pending.image_id,
            packets_sent=sent, duration=time.monotonic() - started,
            rssi=self.last_rssi,
            verification=(
                "sent, unverified: this firmware exposes no flash-ready flag "
                "and no trustworthy slot state, so delivery cannot be confirmed "
                "from the device"
            ),
        )

    # -- device controls ----------------------------------------------------
    async def _async_command(self, payload: bytes) -> None:
        if not await self._async_ensure_connected():
            raise TransportError("the MojiPic is not reachable right now")
        await self.transport.async_write(CHAR_DEVICE_COMMANDS, payload, True)

    async def async_device_on(self) -> None:
        await self._async_command(COMMAND_DEVICE_ON)

    async def async_device_off(self) -> None:
        await self._async_command(COMMAND_DEVICE_OFF)

    async def async_screen_on(self) -> None:
        await self._async_command(COMMAND_SCREEN_ON)

    async def async_screen_off(self) -> None:
        await self._async_command(COMMAND_SCREEN_OFF)

    async def async_clear_screen(self) -> None:
        await self._async_command(COMMAND_CLEAR_SCREEN)

    async def async_set_brightness(self, level: int) -> None:
        """Brightness 0-100.

        FFF9 is advertised `read,write-without-response` on real hardware, so
        this must not use write-with-response.
        """
        level = max(0, min(100, int(level)))
        if not await self._async_ensure_connected():
            raise TransportError("the MojiPic is not reachable right now")
        await self.transport.async_write(CHAR_BRIGHTNESS, bytes([level]), False)

    # -- diagnostics --------------------------------------------------------
    def diagnostics(self, redact_address: bool = True) -> dict[str, Any]:
        """Device state for the diagnostics download."""
        address = self.address
        if redact_address and len(address) >= 8:
            address = f"{address[:2]}:**:**:**:**:{address[-2:]}"
        return {
            "address": address,
            "address_redacted": redact_address,
            "name_prefix": self.name_prefix,
            "connected": self._connected,
            "link_state": self.link_state,
            "pairing_note": (
                "MojiPic does not support Bluetooth pairing or bonding. The "
                "integration remembers the address and reconnects; it is "
                "reported as 'configured', not 'paired'."
            ),
            "rssi_dbm": self.last_rssi,
            "rssi_warn_threshold": RSSI_WARN_THRESHOLD,
            "battery": self.battery,
            "firmware": self.firmware,
            "processor_id": self.processor_id,
            "manufacturer": self.manufacturer,
            "software_revision": self.software_revision,
            "reconnect_attempts": self.reconnect_attempts,
            "stats": dict(self.stats),
            "push_limitations": (
                "FFFD is read-only on this firmware, so there is no fast "
                "slot-switch. Every image change is a full 192-packet push."
            ),
        }
