<div align="center">
  <img src="pixel_motion_detector/logo.png" alt="Pixel Motion Detector — Camera Motion for Home Assistant" width="720">

  ![Version 0.1.3](https://img.shields.io/badge/version-0.1.3-3572A5)
  ![Home Assistant add-on](https://img.shields.io/badge/Home%20Assistant-add--on-18BCF2)
  ![License: MIT](https://img.shields.io/badge/license-MIT-green)
</div>

---

## At a glance

| | |
| --- | --- |
| **Inputs** | Any configured Home Assistant camera entity that provides snapshots |
| **Detection** | Grayscale pixel-change comparison at 160 × 90 resolution |
| **Outputs** | Per-camera MQTT motion binary sensor and event counter |
| **Tuning** | Sensitivity, pixel threshold, polling interval, and clear delay |
| **Footprint** | Lightweight processing; no video recording or third-party image service |

> **Compatibility note:** This add-on polls Home Assistant's `camera_proxy`
> snapshot endpoint. It does not ingest HLS or WebRTC streams directly. A camera
> must expose snapshots through Home Assistant to be monitored.

## How it works

Pixel Motion Detector periodically fetches a still image from each configured
camera, scales it down to a small grayscale frame, and compares it with the
previous frame. When enough pixels change, it turns that camera's motion sensor
**on**. After the scene stays quiet for the configured delay, the sensor turns
**off** again.

The first successful snapshot establishes a baseline and does not count as
motion. The event counter increments only when the sensor changes from off to
on, so a continuous burst of movement counts as one event.

This is simple visual-change detection, not person or object recognition.
Shadows, lighting changes, and image noise may also trigger a detection.

## Install

This project is distributed as a **custom Home Assistant add-on repository**;
it is not currently listed in the official add-on store.

1. In Home Assistant, open **Settings → Add-ons → Add-on Store**.
2. Open the upper-right menu and choose **Repositories**.
3. Add `https://github.com/DirectedHunt42/VideoMotionDetector`.
4. Find and install **Pixel Motion Detector**, configure it, then start it.

## Configure

Set these options in the add-on's **Configuration** tab:

| Option | Default | Description |
| --- | --- | --- |
| `ha_url` | `http://homeassistant:8123` | Home Assistant Core URL reachable from the add-on. |
| `ha_token` | — | Long-lived access token with permission to read the configured cameras. |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname. |
| `mqtt_port` | `1883` | MQTT broker port. |
| `mqtt_username` | — | Optional broker username. |
| `mqtt_password` | — | Optional broker password. |
| `cameras` | `[]` | Camera entity IDs to monitor; each may include a friendly `name`. |
| `sensitivity` | `60` | Range 1–100. Higher values react to smaller changed areas. |
| `pixel_delta` | `25` | Brightness difference (0–255) for an individual pixel to count as changed. |
| `poll_interval` | `1.0` | Seconds between snapshot polling cycles. |
| `motion_off_delay` | `5` | Quiet seconds before a motion sensor turns off. |

Example:

```yaml
ha_url: http://homeassistant:8123
ha_token: YOUR_LONG_LIVED_ACCESS_TOKEN
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: motion_detector
mqtt_password: YOUR_MQTT_PASSWORD
cameras:
  - entity_id: camera.front_door
    name: Front Door
  - entity_id: camera.garage
    name: Garage
sensitivity: 60
pixel_delta: 25
poll_interval: 1.0
motion_off_delay: 5
```

Create a long-lived access token from your Home Assistant user profile. Treat
the token and MQTT password as secrets: do not share or commit them. Configure
the broker to allow the add-on to publish its MQTT topics. The built-in
Mosquitto add-on is a common broker choice.

### Tuning sensitivity

The detector marks a pixel as changed when its grayscale brightness differs
from the previous frame by at least `pixel_delta`. It then checks what
percentage of the 160 × 90 pixels changed:

- **Sensitivity 1:** about 5% of pixels must change.
- **Sensitivity 100:** about 0.1% of pixels must change.

Start with the default value, then adjust sensitivity and `pixel_delta` for your
camera view. A larger pixel threshold filters out small brightness variations;
a higher sensitivity requires fewer changed pixels to report motion.

## Use in automations

MQTT discovery creates two entities per camera. For a camera named `Front Door`,
the entity IDs are typically:

- `binary_sensor.pixel_motion_front_door_motion` — motion state (`on` / `off`)
- `sensor.pixel_motion_front_door_detection_count` — total motion events

Use the binary sensor as a Home Assistant **State** trigger with `to: "on"`.
The count sensor increases at each new motion event and can be used for
dashboards or statistics.

If a snapshot request fails, the add-on logs a warning and retries on the next
poll. MQTT availability is published so Home Assistant can indicate whether
the add-on is connected.

## Development

Run the unit tests with Python 3.11 or newer:

```sh
cd pixel_motion_detector
python -m unittest discover -s tests -v
```

The add-on image is built from `Dockerfile`; Home Assistant supplies the base
image for the selected architecture.

## License

MIT — see [LICENSE.txt](LICENSE.txt).
