"""Coordinator tests: push ordering, supersede, reconnect, honest reporting.

Runs against the fake transport, so the real push pipeline is exercised without
Bluetooth hardware.
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _mjloader  # noqa: E402
from test_integration_offline import FakeTransport  # noqa: E402

const = _mjloader.const
image_encode = _mjloader.image_encode


class _FakeLoop:
    def create_future(self):
        return asyncio.get_event_loop().create_future()


class _FakeHass:
    """Just enough hass for the coordinator: a loop and a task factory."""

    def __init__(self):
        self.loop = _FakeLoop()
        self.dispatched = []

    def async_create_task(self, coro):
        return asyncio.get_event_loop().create_task(coro)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _FakeEntry:
    entry_id = "test"
    title = "Mojipic"
    data = {"address": "AA:BB:CC:DD:EE:FF", "name": "Mojipic"}


def _load_coordinator():
    """Load coordinator.py with the Home Assistant helpers stubbed out."""
    stub = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_send = lambda *a, **k: None
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_time_interval = lambda *a, **k: (lambda: None)
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    import datetime as _datetime

    dt.timedelta = _datetime.timedelta
    dt.utcnow = lambda: _datetime.datetime.now(_datetime.timezone.utc)
    util.dt = dt

    sys.modules.setdefault("homeassistant", stub)
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers.dispatcher"] = dispatcher
    sys.modules["homeassistant.helpers.event"] = event
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = dt
    return _mjloader.load_module("coordinator")


FRAMEBUFFER = image_encode.encode_framebuffer(
    Image.new("RGB", (32, 32), (128, 0, 128)), enhance_image=False
)


def build_coordinator(fake_transport):
    """Build a coordinator wired to fakes, bypassing HA-only constructors."""
    hass = _FakeHass()
    coordinator_mod = _load_coordinator()

    def packets_for_framebuffer(framebuffer, image_id):
        full_id = (const.HEADER_PREFIX_5BIT << 12) | image_id
        return list(image_encode.frame_packets(full_id, framebuffer))

    coord = coordinator_mod.MojipicCoordinator(
        hass, _FakeEntry(), fake_transport,
        packets_for_framebuffer=packets_for_framebuffer,
    )
    coord._unsub_interval = None
    return coord, hass


class TestPushPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_successful_push_writes_192_packets_with_response(self):
        fake = FakeTransport()
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            result = await coord.async_display(FRAMEBUFFER, 0)
            self.assertTrue(result.outcome.ok)
            self.assertEqual(result.packets_sent, 192)
            pixel = [w for w in fake.writes if w[0] == const.CHAR_WRITE_IMAGE]
            self.assertEqual(len(pixel), 192)
            # FFF1 is write-with-response on real hardware.
            self.assertTrue(all(w[2] for w in pixel))
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_unverified_is_reported_not_verified(self):
        """No flash-ready flag exists, so success must not claim verification."""
        fake = FakeTransport()
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            result = await coord.async_display(FRAMEBUFFER, 0)
            self.assertEqual(result.outcome.value, "sent_unverified")
            self.assertIn("unverified", result.verification)
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_mid_transfer_failure_counted(self):
        """Regression: a failure must increment stats['failed']."""
        fake = FakeTransport(fail_write_at=40)
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            result = await coord.async_display(FRAMEBUFFER, 0)
            self.assertEqual(result.outcome.value, "failed")
            self.assertEqual(coord.stats["failed"], 1)
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_supersede_latest_wins(self):
        fake = FakeTransport(slow_write=0.004)
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            slow = asyncio.create_task(coord.async_display(FRAMEBUFFER, 0))
            await asyncio.sleep(0.03)
            fast = await coord.async_display(FRAMEBUFFER, 7)
            first = await slow
            self.assertEqual(first.outcome.value, "abandoned")
            self.assertTrue(fast.outcome.ok)
            self.assertEqual(fast.image_id, 7)
            self.assertGreaterEqual(coord.stats["abandoned"], 1)
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_single_inflight(self):
        fake = FakeTransport(slow_write=0.003)
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        concurrent = 0
        peak = 0
        original = fake.async_write

        async def tracking(uuid, data, response):
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            try:
                return await original(uuid, data, response)
            finally:
                concurrent -= 1

        fake.async_write = tracking
        try:
            await asyncio.gather(*[
                coord.async_display(FRAMEBUFFER, i) for i in range(4)
            ])
            self.assertEqual(peak, 1, "pushes must never overlap")
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_unreachable_fails_fast_not_hangs(self):
        fake = FakeTransport(fail_connect_times=10_000)
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            result = await asyncio.wait_for(
                coord.async_display(FRAMEBUFFER, 0), 20
            )
            self.assertEqual(result.outcome.value, "failed")
            self.assertEqual(coord.stats["failed"], 1)
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_counters_sum_to_attempts(self):
        fake = FakeTransport()
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            for _ in range(3):
                await coord.async_display(FRAMEBUFFER, 0)
            total = (coord.stats["sent"] + coord.stats["failed"]
                     + coord.stats["abandoned"])
            self.assertEqual(total, 3)
        finally:
            coord._running = False
            await coord.async_stop()


class TestDeviceControls(unittest.IsolatedAsyncioTestCase):
    async def test_brightness_uses_no_response(self):
        """Regression: FFF9 is write-without-response only on real hardware."""
        fake = FakeTransport(
            writable_chars={const.CHAR_BRIGHTNESS: {False},
                            const.CHAR_DEVICE_COMMANDS: {True}}
        )
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            await coord.async_set_brightness(150)   # clamped to 100
            bright = [w for w in fake.writes if w[0] == const.CHAR_BRIGHTNESS]
            self.assertEqual(len(bright), 1)
            self.assertEqual(bytes(bright[0][1]), bytes([100]))
            self.assertFalse(bright[0][2])
        finally:
            coord._running = False
            await coord.async_stop()

    async def test_control_payloads(self):
        fake = FakeTransport(writable_chars={const.CHAR_DEVICE_COMMANDS: {True}})
        coord, _ = build_coordinator(fake)
        coord._running = True
        coord._ensure_worker()
        try:
            await coord.async_device_on()
            await coord.async_screen_off()
            await coord.async_clear_screen()
            cmds = [bytes(w[1]) for w in fake.writes
                    if w[0] == const.CHAR_DEVICE_COMMANDS]
            self.assertEqual(cmds, [b"\x02\x01", b"\x02\x05", b"\x07\x01"])
        finally:
            coord._running = False
            await coord.async_stop()


class TestDiagnostics(unittest.IsolatedAsyncioTestCase):
    def test_address_is_redacted_by_default(self):
        fake = FakeTransport()
        coord, _ = build_coordinator(fake)
        diag = coord.diagnostics(redact_address=True)
        self.assertNotIn("AA:BB:CC:DD:EE:FF", str(diag))
        self.assertIn("**", diag["address"])
        # Wording must not imply Bluetooth pairing.
        self.assertIn("does not support Bluetooth pairing", diag["pairing_note"])

    def test_diagnostics_document_the_no_fast_path(self):
        fake = FakeTransport()
        coord, _ = build_coordinator(fake)
        diag = coord.diagnostics()
        self.assertIn("no fast slot-switch", diag["push_limitations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
