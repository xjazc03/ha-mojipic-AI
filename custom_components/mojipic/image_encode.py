"""Image conversion: any image -> the device's 3072-byte RGB565 framebuffer.

The encoding itself is verified-correct and must not be "improved":

    r5 = (pixel >> 19) & 31
    g5 = (pixel >> 11) & 31
    b5 = (pixel >>  3) & 31

These are pure bit-slices of the ARGB pixel, matching the vendor app exactly.
A "better" rounded reduction would be wrong: the hardware is fed by the
vendor's convention. This was confirmed on real hardware, where a solid colour
and a diagonal pattern both rendered correctly.

Two things deliberately differ from the earlier standalone driver, because this
ships to end users:

  * **Aspect-fill crop**, not letterboxing. Stretching distorts faces, and
    letterbox bars waste most of a 32x32 panel.
  * **Contrast/brightness lift.** A 32x32 LED panel renders dark photos poorly;
    icons and emoji are unaffected because they are already high-contrast.
"""

from __future__ import annotations

import io
from typing import Any, Iterable

from .const import (
    BYTES_PER_FRAME,
    CHUNK_BYTES,
    CHUNKS_PER_FRAME,
    HEADER_PREFIX_5BIT,
    MAX_SLOT,
    MIN_SLOT,
    PACKET_BYTES,
    SIZE,
)


class ImageConversionError(ValueError):
    """Raised when an image cannot be converted for the display."""


def aspect_fill(image, size: int = SIZE):
    """Scale and centre-crop to exactly size x size, preserving aspect ratio."""
    from PIL import Image

    src = image.convert("RGB")
    if src.width == 0 or src.height == 0:
        raise ImageConversionError("image has zero width or height")

    target_ratio = size / size
    src_ratio = src.width / src.height

    if src_ratio > target_ratio:
        # Too wide: crop the sides.
        new_width = max(1, round(src.height * target_ratio))
        left = (src.width - new_width) // 2
        src = src.crop((left, 0, left + new_width, src.height))
    elif src_ratio < target_ratio:
        # Too tall: crop the top and bottom, biased slightly upward so faces
        # (usually in the upper half of a photo) survive the crop.
        new_height = max(1, round(src.width / target_ratio))
        top = int((src.height - new_height) * 0.4)
        src = src.crop((0, top, src.width, top + new_height))

    return src.resize((size, size), Image.LANCZOS)


def enhance(image, contrast: float = 1.15, brightness: float = 1.10):
    """Lighten and add contrast so dark photos survive a tiny LED matrix."""
    from PIL import ImageEnhance

    img = image.convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    return img


def encode_framebuffer(image, enhance_image: bool = True) -> bytes:
    """Convert a PIL image into the device's 3072-byte framebuffer."""
    if enhance_image:
        image = enhance(image)
    img = aspect_fill(image)

    pixels = img.load()
    out = bytearray(BYTES_PER_FRAME)
    for y in range(SIZE):
        row = y * SIZE * 3
        for x in range(SIZE):
            r, g, b = pixels[x, y]
            o = row + x * 3
            # Verified bit-slice extraction. Do not change.
            out[o] = (r >> 3) & 0x1F
            out[o + 1] = (g >> 3) & 0x1F
            out[o + 2] = (b >> 3) & 0x1F
    return bytes(out)


def validate_slot(slot: Any) -> int:
    try:
        slot = int(slot)
    except (TypeError, ValueError) as err:
        raise ImageConversionError(f"invalid image id {slot!r}") from err
    if not MIN_SLOT <= slot <= MAX_SLOT:
        raise ImageConversionError(
            f"image id {slot} is out of range ({MIN_SLOT}-{MAX_SLOT})"
        )
    return slot


def frame_packets(device_id: int, data: bytes, frame: int = 0) -> Iterable[bytes]:
    """Build the 192 BLE packets for one frame.

    Packet layout, 20 bytes each:
        [0:2] full_id big-endian
        [2]   chunk index 0..191
        [3]   frame index
        [4:20] 16 bytes of pixel payload
    """
    if len(data) < BYTES_PER_FRAME:
        raise ImageConversionError(
            f"framebuffer is {len(data)} bytes, expected {BYTES_PER_FRAME}"
        )
    base = frame * BYTES_PER_FRAME
    for chunk in range(CHUNKS_PER_FRAME):
        pkt = bytearray(PACKET_BYTES)
        pkt[0] = (device_id >> 8) & 0xFF
        pkt[1] = device_id & 0xFF
        pkt[2] = chunk & 0xFF
        pkt[3] = frame & 0xFF
        start = base + chunk * CHUNK_BYTES
        pkt[4:] = data[start:start + CHUNK_BYTES]
        yield bytes(pkt)


def packets_for_image(image, image_id: int, enhance_image: bool = True) -> list[bytes]:
    """Convert + frame in one call. Convenience for the display path."""
    image_id = validate_slot(image_id)
    data = encode_framebuffer(image, enhance_image=enhance_image)
    return list(frame_packets((HEADER_PREFIX_5BIT << 12) | image_id, data))


def load_image(raw: bytes):
    """Decode image bytes. PNG and JPEG are the supported formats."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise ImageConversionError(
            "could not read that file. MojiPic accepts PNG and JPEG images."
        ) from err
    return img


def image_size(raw: bytes) -> tuple[int, int]:
    """Return (width, height) without fully decoding."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as img:
            return img.width, img.height
    except Exception as err:  # noqa: BLE001
        raise ImageConversionError("could not read that image file") from err
