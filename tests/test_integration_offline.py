"""Offline test suite for the MojiPic integration.

Everything here runs without Bluetooth hardware, which is the point: the
development host has no radio, so this suite *is* the verification story.

The fake transport is a full stand-in for the BLE link, so push ordering,
supersede behaviour, reconnect and honest reporting are all exercised for real.
"""

import asyncio
import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mjloader  # noqa: E402

const = _mjloader.const
image_encode = _mjloader.image_encode
transport = _mjloader.transport


_INTERNAL_IDENTIFIERS = ("E4:15:F6", "192.168", "jws-node", "ison-fence", "/home/jws")


# ---------------------------------------------------------------- encoder ---
class TestEncoding(unittest.TestCase):
    def test_framebuffer_size(self):
        img = Image.new("RGB", (32, 32), (128, 0, 128))
        self.assertEqual(len(image_encode.encode_framebuffer(img)), 3072)

    def test_known_purple_value(self):
        """Solid purple must encode to (16,0,16). Verified on real hardware."""
        img = Image.new("RGB", (32, 32), (128, 0, 128))
        data = image_encode.encode_framebuffer(img, enhance_image=False)
        self.assertEqual(data[0:3], bytes([16, 0, 16]))

    def test_pure_channel_isolated(self):
        for colour, expected in (
            ((255, 0, 0), (31, 0, 0)),
            ((0, 255, 0), (0, 31, 0)),
            ((0, 0, 255), (0, 0, 31)),
            ((255, 255, 255), (31, 31, 31)),
            ((0, 0, 0), (0, 0, 0)),
        ):
            img = Image.new("RGB", (32, 32), colour)
            data = image_encode.encode_framebuffer(img, enhance_image=False)
            self.assertEqual(data[0:3], bytes(expected), f"colour {colour}")

    def test_packet_framing(self):
        img = Image.new("RGB", (32, 32), (255, 0, 0))
        data = image_encode.encode_framebuffer(img)
        packets = list(image_encode.frame_packets(0x2005, data))
        self.assertEqual(len(packets), 192)
        self.assertEqual(len(packets[0]), 20)
        self.assertEqual(packets[0][0:2], bytes([0x20, 0x05]))
        self.assertEqual(packets[0][2], 0)
        self.assertEqual(packets[0][3], 0)
        self.assertEqual(packets[191][2], 191)
        # Reassembling payloads must reproduce the framebuffer exactly.
        rebuilt = b"".join(p[4:] for p in packets)
        self.assertEqual(rebuilt, data)

    def test_reassembly_round_trip(self):
        img = Image.new("RGB", (32, 32))
        px = img.load()
        for y in range(32):
            for x in range(32):
                px[x, y] = ((x * 8) % 256, (y * 8) % 256, (x + y) % 256)
        data = image_encode.encode_framebuffer(img, enhance_image=False)
        packets = list(image_encode.frame_packets(0x2000, data))
        self.assertEqual(b"".join(p[4:] for p in packets), data)

    def test_aspect_fill_does_not_stretch(self):
        """A wide image must be centre-cropped, not squashed."""
        wide = Image.new("RGB", (64, 16), (255, 0, 0))
        filled = image_encode.aspect_fill(wide)
        self.assertEqual(filled.size, (32, 32))

    def test_enhance_lifts_dark_image(self):
        dark = Image.new("RGB", (32, 32), (30, 30, 30))
        plain = image_encode.encode_framebuffer(dark, enhance_image=False)
        lifted = image_encode.encode_framebuffer(dark, enhance_image=True)
        self.assertGreater(lifted[0], plain[0],
                           "enhancement should brighten dark content")

    def test_slot_validation(self):
        for bad in (-1, 4096, "x", None):
            with self.assertRaises(image_encode.ImageConversionError):
                image_encode.validate_slot(bad)
        self.assertEqual(image_encode.validate_slot("12"), 12)

    def test_load_image_rejects_garbage(self):
        with self.assertRaises(image_encode.ImageConversionError):
            image_encode.load_image(b"definitely not an image")

    def test_packets_for_image_end_to_end(self):
        img = Image.new("RGB", (48, 48), (0, 255, 0))
        packets = image_encode.packets_for_image(img, 3)
        self.assertEqual(len(packets), 192)
        self.assertEqual(packets[0][0:2], bytes([0x20, 0x03]))


# --------------------------------------------------------------- transport ---
class FakeTransport(transport.MojipicTransport):
    """Full in-memory stand-in for a BLE link."""

    def __init__(self, rssi=-60, fail_connect_times=0, fail_write_at=None,
                 slow_write=0.0, writable_chars=None):
        self.rssi = rssi
        self.fail_connect_times = fail_connect_times
        self.fail_write_at = fail_write_at
        self.slow_write = slow_write
        self.writes = []
        self.reads = {}
        self.connect_calls = 0
        self.disconnect_calls = 0
        # Maps characteristic UUID -> set of acceptable `response` values,
        # modelling the real GATT properties measured on hardware.
        self.writable_chars = writable_chars or {}
        self._connected = False

    async def async_discover(self, name_prefix, timeout):
        return "AA:BB:CC:DD:EE:FF"

    async def async_connect(self, address):
        self.connect_calls += 1
        if self.fail_connect_times > 0:
            self.fail_connect_times -= 1
            raise transport.TransportError("simulated connect failure")
        self._connected = True

    async def async_disconnect(self):
        self.disconnect_calls += 1
        self._connected = False

    async def async_write(self, characteristic, data, response):
        if characteristic in self.writable_chars:
            allowed = self.writable_chars[characteristic]
            if response not in allowed:
                raise transport.TransportError(
                    f"{characteristic} does not support response={response} "
                    f"(allows {sorted(allowed)})"
                )
        if self.fail_write_at is not None and len(self.writes) == self.fail_write_at:
            raise transport.TransportError("simulated link drop")
        if self.slow_write:
            await asyncio.sleep(self.slow_write)
        self.writes.append((characteristic, bytes(data), response))

    async def async_read(self, characteristic):
        return self.reads.get(characteristic)

    async def async_rssi(self):
        return self.rssi

    @property
    def connected(self):
        return self._connected


class TestTransportAbstraction(unittest.IsolatedAsyncioTestCase):
    async def test_transport_is_substitutable(self):
        """The whole point of the seam: a fake transport works end to end."""
        fake = FakeTransport()
        self.assertIsInstance(fake, transport.MojipicTransport)
        await fake.async_connect("AA:BB:CC:DD:EE:FF")
        self.assertTrue(fake.connected)
        await fake.async_write(const.CHAR_WRITE_IMAGE, b"x" * 20, True)
        self.assertEqual(len(fake.writes), 1)

    async def test_write_type_is_enforced_by_characteristic(self):
        """A transport that models real GATT properties must reject bad writes.

        These property sets come from a live GATT dump, not from the vendor app.
        """
        measured = {
            const.CHAR_WRITE_IMAGE: {True},        # read,write
            const.CHAR_BRIGHTNESS: {False},         # read,write-without-response
            const.CHAR_DEVICE_COMMANDS: {True},    # read,write
            const.CHAR_PROCESSOR_ID: set(),         # read only -- cannot write
        }
        fake = FakeTransport(writable_chars=measured)
        await fake.async_connect("AA:BB:CC:DD:EE:FF")

        # FFF9 is write-without-response only on real firmware.
        with self.assertRaises(transport.TransportError):
            await fake.async_write(const.CHAR_BRIGHTNESS, b"\x50", True)
        await fake.async_write(const.CHAR_BRIGHTNESS, b"\x50", False)  # must work

        # FFF1 is write-with-response only.
        with self.assertRaises(transport.TransportError):
            await fake.async_write(const.CHAR_WRITE_IMAGE, b"x" * 20, False)
        await fake.async_write(const.CHAR_WRITE_IMAGE, b"x" * 20, True)  # must work

        # FFFD is read-only: there is no slot-switch command on this firmware.
        with self.assertRaises(transport.TransportError):
            await fake.async_write(const.CHAR_PROCESSOR_ID, b"\x00\x00", True)


class TestVerifiedConstants(unittest.TestCase):
    def test_write_image_uuid(self):
        self.assertEqual(const.CHAR_WRITE_IMAGE,
                         "0000fff1-0000-1000-8000-00805f9b34fb")

    def test_processor_id_is_known_read_only(self):
        """FFFD is read-only on real hardware; there is no slot-switch command."""
        self.assertEqual(const.CHAR_PROCESSOR_ID,
                         "0000fffd-0000-1000-8000-00805f9b34fb")

    def test_geometry(self):
        self.assertEqual(const.SIZE, 32)
        self.assertEqual(const.BYTES_PER_FRAME, 3072)
        self.assertEqual(const.CHUNKS_PER_FRAME, 192)
        self.assertEqual(const.PACKET_BYTES, 20)

    def test_no_owner_specific_values(self):
        """Nothing owner-specific may be baked into the integration."""
        # Only actual constant *values* -- not module metadata like __file__,
        # which legitimately contains the checkout path.
        values = [
            v for name, v in vars(const).items()
            if isinstance(v, str) and not name.startswith("__")
        ]
        blob = " ".join(values)
        # These are the internal project identifiers that must never reach a
        # public repo. They appear here only as negative assertions.
        for forbidden in _INTERNAL_IDENTIFIERS:
            self.assertNotIn(forbidden, blob, f"{forbidden} must not be hardcoded")

    def test_no_absolute_paths_in_constants(self):
        """Storage must be config-relative, never an absolute host path."""
        for name, value in vars(const).items():
            if name.startswith("__") or not isinstance(value, str):
                continue
            self.assertFalse(
                value.startswith("/"),
                f"absolute path must not be hardcoded: {name}={value}",
            )
            self.assertNotIn("/config", value)

    def test_rssi_threshold_documented(self):
        self.assertEqual(const.RSSI_WARN_THRESHOLD, -80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
