"""Diagnostics support for MojiPic.

The BLE address is redacted by default: it is quasi-identifying, and diagnostics
files get pasted into public issue trackers.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"address", "processor_id", "path", "framebuffer_path"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The BLE address is masked unless the user has explicitly opted in, because
    it identifies a physical device and diagnostics are shared publicly.
    """
    bundle = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if bundle is None:
        return {"error": "config entry not loaded"}

    coordinator = bundle["coordinator"]
    manager = bundle["manager"]

    redact = not hass.data.get(DOMAIN, {}).get("_allow_address_in_diagnostics", False)

    images = []
    try:
        stored = await manager.async_list()
    except Exception:  # noqa: BLE001
        stored = []
    for item in stored:
        images.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "filename": item.get("filename"),
                "width": item.get("width"),
                "height": item.get("height"),
                "size_bytes": item.get("size_bytes"),
            }
        )

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": dict(entry.data),
            },
            "coordinator": coordinator.diagnostics(redact_address=redact),
            "images": images,
            "image_count": len(images),
        },
        TO_REDACT,
    )
