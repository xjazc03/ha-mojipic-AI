# MojiPic for Home Assistant

Push images to a **MojiPic** 32×32 RGB LED matrix display from Home Assistant, so the
display reacts to things happening in your house — doorbell, motion at night, a package
at the door, anything you automate.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.10%2B-41BDF5.svg)](https://www.home-assistant.io)

---

## What it does

`mojipic.display_image` pushes any image to the panel. Images are resized to fit
32×32 (letterboxed, not stretched) and encoded to the device's native RGB565 format.

That's the whole point of this integration — **event → image**. The rest is
supporting cast.

## Requirements

- Home Assistant **2024.10.0** or newer
- A MojiPic, powered on and within BLE range (~10 m) of the machine Home Assistant
  is running on
- **A Bluetooth adapter Home Assistant can reach.** Any of these work:
  - a local Bluetooth adapter on the HA host (via a USB passthrough / add-on)
  - an **ESPHome Bluetooth proxy** near the display (recommended — lets the HA
    server sit anywhere)
  - the built-in adapter, if your HA host exposes one

> **Important:** MojiPic is BLE-only with no Wi-Fi or network interface. Home
> Assistant cannot reach it over the network. Whatever pushes the image must be
> physically within Bluetooth range of the display.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository of type *Integration*.
2. Install **MojiPic**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/mojipic` into your Home Assistant `config/custom_components/`
directory and restart.

## Configuration

**Settings → Devices & Services → Add Integration → MojiPic**

Enter the BLE address (`AA:BB:CC:DD:EE:FF`), or tick **Scan** and let Home Assistant
find it for you. Not sure of the address? Open the MojiPic vendor app once — it
lists the device. Or check the Home Assistant **Bluetooth** integration, which lists
everything it can see.

## Usage

### The main event

```yaml
action: mojipic.display_image
data:
  path: mojipic_images/doorbell.png
```

### A realistic automation

Package left at the door → show a 📦:

```yaml
automation:
  - alias: "MojiPic - package delivered"
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door_package
        to: "on"
    actions:
      - action: mojipic.display_image
        data:
          path: mojipic_images/package.png
```

Doorbell → show a bell:

```yaml
automation:
  - alias: "MojiPic - doorbell"
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door
        to: "on"
    actions:
      - action: mojipic.display_image
        data:
          path: mojipic_images/doorbell.png
```

### Pulling an image from a camera at the moment of an event

```yaml
automation:
  - alias: "MojiPic - who's at the door"
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door
        to: "on"
    actions:
      - action: mojipic.display_image
        data:
          url: "http://192.168.8.50:8123/api/camera_proxy/camera.front_door?width=320"
```

Any non-square image is letterboxed to 32×32 automatically.

## Services

| Service | Description |
|---|---|
| `mojipic.display_image` | **Main service.** Push an image. Accepts `image` (a file/URL/image entity), `path`, or `url`. Optional `brightness`. |
| `mojipic.set_brightness` | Set brightness, 0–100. |
| `mojipic.device_on` / `device_off` | Power the display on/off. |
| `mojipic.screen_on` / `screen_off` | Wake / blank the panel. |
| `mojipic.clear_screen` | Clear to black. |

If you have more than one MojiPic, pass `address:` to target a specific one.

## Entities

| Entity | Type | Notes |
|---|---|---|
| `<name> image` | `image` | The last image pushed — handy for a dashboard or a glanceable history. |
| `<name> battery` | `sensor` | Battery percentage, polled every 15 minutes. |

## Using images stored in `config/`

Images referenced by `path:` must live under your Home Assistant **config
directory** (a `mojipic_images/` subfolder is a good choice). Paths outside it are
rejected.

## Performance — please read

A full image push is **192 BLE writes and takes a few seconds.** It is not instant,
and there is no shortcut:

> MojiPic's firmware exposes the "display stored image N" characteristic
> (`FFFD`) as **read-only**, so there is no way to pre-load images and switch
> between them quickly. Every image change requires a full re-push.

Practical consequences:

- Don't fire two `display_image` calls within a few seconds of each other — they
  will collide on the BLE connection. If you need a sequence, add a `delay:`.
- Because uploads take seconds, **avoid triggering on noisy sensors** (motion with
  a short cooldown, say) or you'll spend more time uploading than the display is
  useful.
- A sensible pattern is to debounce your trigger:

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
      - action: mojipic.display_image
        data:
          path: mojipic_images/hello.png
```

## Troubleshooting

**"not reachable" / nothing happens**
- Is it powered on and in range? BLE is short-range; walls matter.
- Is the vendor app open and holding a connection? Close it — Android/iOS apps
  often keep BLE connections open and MojiPic allows only one.
- Does Home Assistant see the device? Check the **Bluetooth** integration.
- If you're using an ESPHome proxy, confirm it's powered and advertising.

**Image looks wrong**
- Anything not square will be letterboxed with black bars. That's intentional.
- Only 5 bits per channel (32 levels of red/green/blue) are available, so very
  subtle gradients band a little. That's the hardware.

**Upload fails partway**
- Move the ESPHome proxy closer, or reduce interference.
- Very large source images take longer to encode — resize before sending.

## How this was built

This is an unofficial integration. The BLE protocol was reverse-engineered from
the vendor's Android app (Mojipic v4.2.0-R) and then **verified against real
hardware** — a solid-colour test image and a diagonal-stripe pattern were pushed
to a physical MojiPic and confirmed visually, which validates channel order, bit
depth, row ordering and chunk framing.

No affiliation with or endorsement from Infinitum Trading / IIP Brothers.

## Credits

Protocol research and implementation by the owner of this repository.
