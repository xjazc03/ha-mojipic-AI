"""Verify the HACS integration's protocol module against the hardware-verified
implementation that was validated on a real MojiPic on 2026.10.10.

These are the bytes that were pushed to the panel and confirmed correct by eye,
so any divergence here is a real regression.
"""

import importlib.util
import sys
import types
import unittest

from PIL import Image

sys.path.insert(0, "/home/jws/mojipic")

import mojipic as verified  # noqa: E402  (the live-test driver)

# Import const.py and protocol.py without triggering the package __init__, which
# would pull in Home Assistant dependencies that aren't needed to verify bytes.
# protocol.py uses a relative `from .const import ...`, so give the stub package
# a proper __path__ and register it in sys.modules first.
_PKG_PATH = "/home/jws/ha-mojipic/custom_components/mojipic"

_stub = types.ModuleType("mjpkg")
_stub.__path__ = [_PKG_PATH]
sys.modules["mjpkg"] = _stub


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"mjpkg.{name}", f"{_PKG_PATH}/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"mjpkg.{name}"] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
protocol = _load("protocol")


class TestAgainstVerifiedDriver(unittest.TestCase):
    """The integration must produce exactly what the real device accepted."""

    def test_encode_matches_live_verified_encoder(self):
        import random

        for seed in range(8):
            random.seed(seed)
            px = [(random.randrange(256),) * 3 for _ in range(32 * 32)]
            img = Image.new("RGB", (32, 32))
            img.putdata(px)
            self.assertEqual(
                protocol.encode_image(img),
                verified.encode_image(img),
                f"seed {seed}: integration encoder diverged from the live-verified one",
            )

    def test_specified_test_images_match(self):
        for name in ("test_a_purple.png", "test_b_diagonals.png"):
            img = Image.open(f"/home/jws/mojipic/{name}")
            self.assertEqual(
                protocol.encode_image(img), verified.encode_image(img), name
            )

    def test_packets_match_live_verified_framing(self):
        img = Image.open("/home/jws/mojipic/test_b_diagonals.png")
        data = protocol.encode_image(img)
        for slot in (0, 1, 42, 4095):
            full_id = (const.HEADER_PREFIX_5BIT << 12) | slot
            mine = list(protocol.frame_packets(full_id, data))
            theirs = list(verified.frame_packets(full_id, 0, data))
            self.assertEqual(len(mine), 192)
            self.assertEqual(mine, theirs, f"slot {slot} framing diverged")

    def test_known_colour_values(self):
        """Purple (128,0,128) must encode to (16,0,16) -- owner-verified on panel."""
        img = Image.open("/home/jws/mojipic/test_a_purple.png")
        enc = protocol.encode_image(img)
        self.assertEqual(enc[0:3], bytes([16, 0, 16]))
        self.assertEqual(len(set(zip(enc[0::3], enc[1::3], enc[2::3]))), 1)

    def test_slot_validation(self):
        for bad in (-1, 4096, "abc", None):
            with self.assertRaises(protocol.MojipicProtocolError):
                protocol.validate_slot(bad)
        self.assertEqual(protocol.validate_slot("7"), 7)

    def test_letterbox_does_not_distort(self):
        """A non-square image must fit inside 32x32, not stretch to it."""
        wide = Image.new("RGB", (64, 16), (255, 0, 0))
        enc = protocol.encode_image(wide)
        self.assertEqual(len(enc), 3072)
        # Red bar should occupy the vertical middle only (16 rows scaled to 8).
        rows_with_red = sum(
            1
            for y in range(32)
            if enc[(y * 32 * 3)] == 31 and enc[(y * 32 * 3) + 1] == 0
        )
        self.assertGreater(rows_with_red, 0)
        self.assertLess(rows_with_red, 32, "wide image should be letterboxed, not stretched")

    def test_load_image_rejects_garbage(self):
        with self.assertRaises(protocol.MojipicProtocolError):
            protocol.load_image(b"this is not an image")


if __name__ == "__main__":
    unittest.main(verbosity=2)
