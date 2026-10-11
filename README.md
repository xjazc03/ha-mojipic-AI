# MojiPic for Home Assistant

Push images to a **MojiPic** 32×32 RGB LED matrix display from Home Assistant, so the
display reacts to things happening in your house — doorbell, motion at night, a package
at the door, anything you automate.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.10%2B-41BDF5.svg)](https://www.home-assistant.io)

---

## What it does

Upload images once, then display them on the panel from automations or straight from a
dashboard. Images are cropped to fit 32×32, brightened, and encoded to the display's
native RGB565 format.

The point is **event → image**. The rest is supporting cast.

---

## Requirements

- Home Assistant **2024.10.0** or newer
- A MojiPic, powered on and within Bluetooth range (~10 m)
- **A Bluetooth adapter Home Assistant can reach:**
  - a local adapter on the HA host
  - an **ESPHome Bluetooth proxy** placed near the display — usually the most reliable
    option, and it lets your HA server live anywhere
  - the built-in adapter, if your HA host exposes one

> MojiPic is BLE-only. It has no Wi-Fi and no network interface, so Home Assistant
> cannot reach it over the network. Whatever pushes the image must be physically
> within Bluetooth range.

---

## Installation

### HACS

1. Add this repository as a custom repository of type *Integration*.
2. Install **MojiPic**.
3. Restart Home Assistant.

### Manual

Copy `integrations/mojipic/` into your Home Assistant `config/custom_components/mojipic/`
and restart.

---

## Setup

**Settings → Devices & Services → Add Integration → MojiPic**

Leave **Scan for a MojiPic** ticked and Home Assistant will find it, or type the BLE
address yourself (`AA:BB:CC:DD:EE:FF`). Don't know the address? Open the MojiPic vendor
app once and look at the device list, or check the Home Assistant **Bluetooth**
integration.

### Uploading images

Once added, the device page offers an **Upload image** action. Drop in a PNG or JPEG and
it is stored under `config/mojipic/images/`, with a pre-converted framebuffer cached
alongside it so displaying never re-encodes.

You can also upload from an automation:

```yaml
action: mojipic.upload_image
data:
  filename: doorbell.png
  image_path: mojipic_images/doorbell.png
```

### Showing images

From an automation:

```yaml
automation:
  - alias: "MojiPic - doorbell"
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door
        to: "on"
    actions:
      - action: mojipic.display_stored
        data:
          image_id: 0
```

Or pick an image straight from a dashboard using the **MojiPic card** (below), or the
`select` entity.

---

## Lovelace card

Copy `integrations/mojipic/panel/mojipic-card.js` to `config/www/mojipic-card.js`, then
add as a dashboard resource:

```yaml
url: /local/mojipic-card.js
type: module
```

```yaml
type: custom:mojipic-card
image_entity: image.mojipic_image
select_entity: select.mojipic_image
```

---

## Services

| Service | Description |
|---|---|
| `mojipic.upload_image` | Store an image. `image_data` (base64) or `image_path` + `filename`. |
| `mojipic.display_stored` | Display a previously uploaded image by `image_id`. |
| `mojipic.display_image` | Display a file directly, without storing it first. |
| `mojipic.delete_image` | Remove a stored image. |
| `mojipic.set_brightness` | Brightness, 0–100. |
| `mojipic.clear_screen` | Clear to black. |
| `mojipic.device_on` / `device_off` | Power the display on/off. |
| `mojipic.screen_on` / `screen_off` | Wake / blank the panel. |
| `mojipic.rescan` | Look for the display again and update the remembered address. |

With more than one display, add `address:` to target a specific one.

## Entities

| Entity | Type | Notes |
|---|---|---|
| `<name> image` | `image` | The image currently on the display. |
| `<name> image` | `select` | Choose which stored image to show. |
| `<name> battery` | `sensor` | Battery percentage. |

---

## Performance — please read

A full image push is **192 Bluetooth writes and takes a few seconds.** There is no
shortcut:

> MojiPic's firmware exposes the "display stored image N" characteristic as
> **read-only**, so images cannot be pre-loaded and switched quickly. **Every image
> change is a full transfer.**

Practical consequences:

- Don't fire two display calls within a few seconds of each other — the newer one wins
  and abandons the older, which is intentional but worth knowing.
- **Debounce noisy triggers.** Motion sensors in particular.
- A sensible pattern:

```yaml
automation:
  - alias: "MojiPic - motion (debounced)"
    triggers:
      - trigger: state
        entity_id: binary_sensor.hall_motion
        to: "on"
        for:
          seconds: 5
    actions:
      - action: mojipic.display_stored
        data:
          image_id: 1
```

---

## Honest limitations

**This section is the important one.**

- **On-host verification of in-process Bluetooth writes is pending.** The BLE protocol
  was reverse-engineered and verified against real hardware, but pushing through Home
  Assistant's own Bluetooth stack has not yet been confirmed on a live Home Assistant
  install. Home Assistant's Bluetooth support is primarily built around passive
  advertisement monitoring; a MojiPic push is 192 *active* writes. If pushes fail on
  your setup, please open an issue — the transport is a single swappable class, so it is
  an isolated fix rather than a rewrite.

- **A successful push is reported as "sent, unverified."** This firmware exposes no
  flash-ready flag and no trustworthy slot state, so the integration cannot confirm from
  the device that an image arrived. It reports what is actually true rather than
  claiming a verification the hardware cannot support.

- **Bluetooth range is the main practical limit.** BLE is roughly 10 m and walls matter.
  If pushes are intermittent, put an ESPHome Bluetooth proxy near the display. The
  integration logs a warning below **−80 dBm**.

- **Photographs at 32×32 are inherently rough.** Icons, emoji and simple graphics look
  far better. A detailed photo will always look coarse — that is the hardware, not a bug.

- **Only 5 bits per channel** (32 levels of red, green and blue), so very subtle
  gradients band a little.

- **Non-square images are centre-cropped**, not letterboxed, because stretching distorts
  faces.

- **MojiPic does not support Bluetooth pairing or bonding.** The integration remembers
  the device address and reconnects on that basis. If a firmware update or a battery
  event changes the address, use `mojipic.rescan` or the repair flow — it finds the
  display again and re-persists the address.

---

## Troubleshooting

**"Not found" during setup**
Make sure the display is powered on, in range, and **not connected to the vendor app** —
Android and iOS apps often hold a BLE connection open, and this display allows only one
at a time. Moving the display usually fixes a failed scan.

**Push fails or is intermittent**
Check the RSSI warning in the log (below −80 dBm means trouble). Move the adapter or add
a Bluetooth proxy closer to the display.

**An image looks wrong**
Non-square images are cropped centrally by design. Only 5 bits per channel are available,
so fine gradients band. Very dark photos are brightened automatically, but very dark
artwork may still need lightening beforehand.

**Battery reads oddly**
MojiPic is a 12V automotive accessory. A reading usually means it is charging, or running
on its internal pack.

---

## How this was built

Unofficial integration. The BLE protocol was reverse-engineered from the vendor's Android
app and then **verified against real hardware**: a solid-colour image and a
diagonal-stripe pattern were pushed to a physical MojiPic and confirmed visually, which
validates channel order, bit depth, row ordering and chunk framing.

Those tests also corrected several assumptions the vendor's app makes about its own
firmware, which this integration follows instead:

- the pixel characteristic needs **write-with-response**, not write-without-response
- the "show stored image" characteristic is **read-only** — no fast slot-switch exists
- the status characteristic **never** reports a flash-ready flag

No affiliation with or endorsement from the device manufacturer.

MIT licensed.
