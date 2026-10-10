"""MojiPic image encoding and BLE packet framing.

Derived from the vendor APK (Mojipic v4.2.0-R, com.mojipic.tools_bluetooth_kit),
decompiled 2026.10.10, and validated against real hardware the same day: both a
solid colour and a diagonal-stripe pattern rendered correctly on the panel,
confirming channel order, 5-bit scaling, row-major ordering and chunk offsets.

Encoding: RGB565 -- 5 bits per channel, 3 bytes per pixel, 3072 bytes per frame.

Packet framing (Writer.dataBuffer in the APK) -- 20 bytes per BLE write:

    byte 0 : high byte of full_id
    byte 1 : low  byte of full_id
    byte 2 : chunk index, 0..191
    byte 3 : frame index (animation)
    byte 4..19 : 16 bytes of pixel payload

    full_id = (HEADER_PREFIX_5BIT << 12) | slot      slot is 0..4095
    payload offset = (frame * 3072) + (chunk * 16) + i
"""

from __future__ import annotations

import io
from typing import AsyncIterator, Iterable

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


class MojipicProtocolError(ValueError):
    """Raised for an invalid slot or malformed image."""


def encode_image(image) -> bytes:
    """Encode a PIL image into the device's 3072-byte 5-bit framebuffer.

    Non-square and non-32x32 images are resized (contain-fit, centered) so that
    callers can hand over an arbitrary image without pre-processing it.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    if image.size != (SIZE, SIZE):
        # Letterbox rather than stretch: a squashed emoji reads much worse than a
        # correctly-proportioned one with black bars.
        image = _contain_to_canvas(image)

    pixels = image.load()
    out = bytearray(BYTES_PER_FRAME)
    for y in range(SIZE):
        row = y * SIZE * 3
        for x in range(SIZE):
            r, g, b = pixels[x, y]
            o = row + x * 3
            # Pure bit-slices of the ARGB pixel, matching the vendor app exactly.
            out[o] = (r >> 3) & 0x1F
            out[o + 1] = (g >> 3) & 0x1F
            out[o + 2] = (b >> 3) & 0x1F
    return bytes(out)


def _contain_to_canvas(image):
    """Scale an image to fit inside 32x32, centered on a black canvas."""
    from PIL import Image

    canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    src = image.copy()
    src.thumbnail((SIZE, SIZE), Image.LANCZOS)
    offset = ((SIZE - src.width) // 2, (SIZE - src.height) // 2)
    canvas.paste(src, offset)
    return canvas


def validate_slot(slot: int) -> int:
    """Coerce and range-check a device slot id."""
    try:
        slot = int(slot)
    except (TypeError, ValueError) as err:
        raise MojipicProtocolError(f"invalid slot {slot!r}") from err
    if not MIN_SLOT <= slot <= MAX_SLOT:
        raise MojipicProtocolError(
            f"slot {slot} out of range {MIN_SLOT}..{MAX_SLOT}"
        )
    return slot


def frame_packets(device_id: int, data: bytes, frame: int = 0) -> Iterable[bytes]:
    """Build the 192 BLE packets for one frame."""
    if len(data) < BYTES_PER_FRAME:
        raise MojipicProtocolError(
            f"payload {len(data)} bytes, expected {BYTES_PER_FRAME}"
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


def packets_for_image(image, slot: int) -> list[bytes]:
    """Convenience: encode + frame in one call."""
    slot = validate_slot(slot)
    data = encode_image(image)
    return list(frame_packets((HEADER_PREFIX_5BIT << 12) | slot, data))


def load_image(raw: bytes):
    """Decode image bytes (PNG/JPEG/GIF/BMP) into a PIL image."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise MojipicProtocolError(
            "could not decode image data; expected PNG, JPEG, GIF or BMP"
        ) from err
    return img


async def iter_packets(packets: list[bytes], progress=None) -> AsyncIterator[bytes]:
    """Yield packets, optionally reporting 0-100 progress."""
    total = len(packets)
    for index, pkt in enumerate(packets):
        yield pkt
        if progress is not None and index % 16 == 0:
            progress(min(100, int(index * 100 / total)))
    if progress is not None:
        progress(100)
