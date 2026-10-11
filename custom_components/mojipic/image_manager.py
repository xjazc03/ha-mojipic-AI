"""Image storage and selection for MojiPic.

Images live under the Home Assistant config directory:

    <config>/mojipic/images/<id>.png        original upload
    <config>/mojipic/framebuffers/<id>.bin  pre-converted 3072-byte framebuffer
    <config>/mojipic/manifest.json          id -> metadata

The framebuffer is cached at upload time so displaying an image never re-encodes
it. That matters because a push is already slow (192 BLE writes) and there is no
device-side fast path to fall back on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from homeassistant.core import HomeAssistant

from .const import (
    BYTES_PER_FRAME,
    UPLOAD_CANDIDATE_DIRS,
    FRAMEBUFFER_SUFFIX,
    IMAGE_STORAGE_BIN_DIR,
    IMAGE_STORAGE_DIR,
    MANIFEST_FILENAME,
    MAX_SLOT,
    MIN_SLOT,
    STORAGE_DIR,
    SUPPORTED_UPLOAD_EXTENSIONS,
)
from .image_encode import (
    ImageConversionError,
    encode_framebuffer,
    image_size,
    load_image,
    validate_slot,
)

_LOGGER = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    """Make an arbitrary user filename safe to write to disk."""
    base = os.path.basename(name or "").strip()
    base = _SAFE_NAME.sub("_", base)
    base = base.lstrip(".") or "image"
    return base[:120]


class ImageManager:
    """Stores, indexes and retrieves displayable images."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._manifest: Optional[dict[str, dict[str, Any]]] = None

    # -- paths --------------------------------------------------------------
    @property
    def root(self) -> Path:
        return Path(self.hass.config.path(STORAGE_DIR))

    @property
    def image_dir(self) -> Path:
        return Path(self.hass.config.path(IMAGE_STORAGE_DIR))

    @property
    def framebuffer_dir(self) -> Path:
        return Path(self.hass.config.path(IMAGE_STORAGE_BIN_DIR))

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    def _ensure_dirs(self) -> None:
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.framebuffer_dir.mkdir(parents=True, exist_ok=True)

    # -- manifest -----------------------------------------------------------
    async def async_load(self) -> dict[str, dict[str, Any]]:
        """Read the manifest from disk, creating an empty one if absent."""
        if self._manifest is not None:
            return self._manifest
        await self.hass.async_add_executor_job(self._load_sync)
        return self._manifest  # type: ignore[return-value]

    def _load_sync(self) -> None:
        try:
            with open(self.manifest_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._manifest = data
                return
            _LOGGER.warning("MojiPic manifest was malformed; starting empty")
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as err:
            _LOGGER.warning("Could not read MojiPic manifest (%s); starting empty", err)
        self._manifest = {}

    def _save_sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._manifest or {}, handle, indent=2, sort_keys=True)
        tmp.replace(self.manifest_path)

    async def async_save(self) -> None:
        await self.hass.async_add_executor_job(self._save_sync)

    # -- operations ---------------------------------------------------------
    async def async_list(self) -> list[dict[str, Any]]:
        """All stored images, newest first."""
        manifest = await self.async_load()
        return sorted(
            manifest.values(),
            key=lambda item: item.get("uploaded_at", ""),
            reverse=True,
        )

    async def async_get(self, image_id: int) -> Optional[dict[str, Any]]:
        manifest = await self.async_load()
        return manifest.get(str(image_id))

    def _next_free_id(self) -> int:
        manifest = self._manifest or {}
        used = {int(k) for k in manifest if str(k).lstrip("-").isdigit()}
        for candidate in range(MIN_SLOT, MAX_SLOT + 1):
            if candidate not in used:
                return candidate
        raise ImageConversionError(
            "no free image slots remain (4096 maximum). Delete an image first."
        )

    async def async_upload(
        self, raw: bytes, filename: str = "image.png", image_id: Optional[int] = None
    ) -> dict[str, Any]:
        """Store an image and pre-convert its framebuffer.

        Accepts PNG and JPEG. Anything smaller than 32x32 is upscaled rather
        than rejected, because refusing a tiny icon is unhelpful.
        """
        if not raw:
            raise ImageConversionError("the uploaded file was empty")

        extension = os.path.splitext(filename or "")[1].lower()
        if extension and extension not in SUPPORTED_UPLOAD_EXTENSIONS:
            # Be forgiving about the extension if the bytes are actually fine.
            try:
                image_size(raw)
            except ImageConversionError as err:
                raise ImageConversionError(
                    f"unsupported file type '{extension}'. "
                    "Please upload a PNG or JPEG image."
                ) from err

        width, height = image_size(raw)
        img = load_image(raw)

        if width < 32 or height < 32:
            _LOGGER.debug(
                "Upscaling %sx%s image to the 32x32 panel", width, height
            )

        data = encode_framebuffer(img)

        return await self.hass.async_add_executor_job(
            self._store_sync, raw, data, filename, width, height, image_id
        )

    def _store_sync(
        self,
        raw: bytes,
        data: bytes,
        filename: str,
        width: int,
        height: int,
        image_id: Optional[int],
    ) -> dict[str, Any]:
        self._ensure_dirs()
        if self._manifest is None:
            self._load_sync()

        target_id = self._next_free_id() if image_id is None else validate_slot(image_id)
        safe = sanitize_filename(filename)

        # Keep the original extension so the stored file stays openable.
        extension = os.path.splitext(safe)[1].lower()
        if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
            safe = f"{os.path.splitext(safe)[0]}.png"

        image_path = self.image_dir / f"{target_id}_{safe}"
        framebuffer_path = self.framebuffer_dir / f"{target_id}{FRAMEBUFFER_SUFFIX}"

        with open(image_path, "wb") as handle:
            handle.write(raw)
        with open(framebuffer_path, "wb") as handle:
            handle.write(data)

        entry = {
            "id": target_id,
            "name": os.path.splitext(safe)[0],
            "filename": safe,
            "path": str(image_path),
            "framebuffer_path": str(framebuffer_path),
            "width": width,
            "height": height,
            "size_bytes": len(raw),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._manifest[str(target_id)] = entry
        self._save_sync()
        _LOGGER.debug("Stored MojiPic image %s (%sx%s)", target_id, width, height)
        return entry

    async def async_delete(self, image_id: int) -> bool:
        image_id = validate_slot(image_id)
        manifest = await self.async_load()
        entry = manifest.pop(str(image_id), None)
        if entry is None:
            return False
        await self.hass.async_add_executor_job(self._remove_files, entry)
        await self.async_save()
        return True

    @staticmethod
    def _remove_files(entry: dict[str, Any]) -> None:
        for key in ("path", "framebuffer_path"):
            target = entry.get(key)
            if not target:
                continue
            try:
                Path(target).unlink(missing_ok=True)
            except OSError as err:
                _LOGGER.warning("Could not delete %s: %s", target, err)

    async def async_framebuffer(self, image_id: int) -> Optional[bytes]:
        """Return the cached 3072-byte framebuffer for a stored image."""
        image_id = validate_slot(image_id)
        entry = await self.async_get(image_id)
        if entry is None:
            return None

        def _read() -> Optional[bytes]:
            try:
                with open(entry["framebuffer_path"], "rb") as handle:
                    return handle.read()
            except OSError:
                return None

        data = await self.hass.async_add_executor_job(_read)
        if data is not None and len(data) == BYTES_PER_FRAME:
            return data

        # Cache missing or stale: re-encode from the original.
        _LOGGER.debug("Re-encoding framebuffer for image %s", image_id)
        raw = await self.hass.async_add_executor_job(self._read_original, entry)
        if raw is None:
            return None
        img = load_image(raw)
        rebuilt = encode_framebuffer(img)
        await self.hass.async_add_executor_job(self._write_framebuffer, entry, rebuilt)
        return rebuilt

    @staticmethod
    def _read_original(entry: dict[str, Any]) -> Optional[bytes]:
        try:
            with open(entry["path"], "rb") as handle:
                return handle.read()
        except OSError:
            return None

    @staticmethod
    def _write_framebuffer(entry: dict[str, Any], data: bytes) -> None:
        try:
            with open(entry["framebuffer_path"], "wb") as handle:
                handle.write(data)
        except OSError as err:
            _LOGGER.debug("Could not refresh framebuffer cache: %s", err)


class UploadResolutionError(ImageConversionError):
    """A file chosen in the upload flow could not be found on disk."""


def resolve_upload_path(hass: HomeAssistant, value: str) -> tuple[bytes, str]:
    """Resolve a file chosen in the upload UI to (raw bytes, filename).

    Home Assistant's `file` selector is not consistent about what it returns:

      * picking an existing file under the config directory yields a *relative
        path* such as ``mojipic_images/doorbell.png``
      * uploading a new file through the picker yields a *UUID* with no
        separators

    Both are supported. UUIDs are looked for in the locations Home Assistant
    uses for uploads, in order, and the search never escapes the config
    directory.
    """
    from pathlib import Path

    value = (value or "").strip()
    if not value:
        raise UploadResolutionError("no file was selected")

    config_dir = Path(hass.config.config_dir).resolve()
    candidates = []

    looks_like_uuid = (
        "/" not in value
        and "\\" not in value
        and len(value) == 32
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )

    if looks_like_uuid:
        for folder in UPLOAD_CANDIDATE_DIRS:
            candidates.append(config_dir / folder / value if folder
                              else config_dir / value)
    else:
        # Relative path chosen from the config directory.
        candidates.append(config_dir / value.lstrip("/"))

    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        # Never read outside the config directory.
        if config_dir not in resolved.parents and resolved != config_dir:
            _LOGGER.warning("Refusing to read %s: outside config", resolved)
            continue
        if resolved.is_file():
            try:
                return resolved.read_bytes(), resolved.name
            except OSError as err:
                raise UploadResolutionError(
                    f"Could not read {resolved.name}: {err}"
                ) from err

    tried = ", ".join(str(c) for c in candidates)
    raise UploadResolutionError(
        f"Could not find that file. Looked in: {tried}. "
        "If you uploaded it, it may still be copying; otherwise put the image "
        "in your Home Assistant config directory and pick it again."
    )
