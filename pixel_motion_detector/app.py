#!/usr/bin/env python3
"""Poll Home Assistant camera snapshots and publish MQTT motion entities."""

import asyncio
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import aiomqtt
from PIL import Image, ImageChops

LOGGER = logging.getLogger("pixel_motion_detector")
OPTIONS_PATH = Path("/data/options.json")
FRAME_SIZE = (160, 90)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
DISCOVERY_PREFIX = "homeassistant"
TOPIC_PREFIX = "pixel_motion"


@dataclass(frozen=True)
class Camera:
    entity_id: str
    name: str
    object_id: str


class MotionTracker:
    def __init__(self, sensitivity: int, pixel_delta: int, off_delay: int) -> None:
        self.sensitivity = sensitivity
        self.pixel_delta = pixel_delta
        self.off_delay = off_delay
        self.previous: Image.Image | None = None
        self.is_motion = False
        self.last_motion_at: float | None = None
        self.detection_count = 0

    def update(self, frame: Image.Image, now: float) -> tuple[bool, bool]:
        current = frame.convert("L").resize(FRAME_SIZE, Image.Resampling.BILINEAR)
        if self.previous is None:
            self.previous = current
            return self.is_motion, False

        difference = ImageChops.difference(self.previous, current)
        self.previous = current
        changed = difference.point(
            lambda value: 255 if value >= self.pixel_delta else 0
        ).histogram()[255]
        changed_percent = changed * 100 / (FRAME_SIZE[0] * FRAME_SIZE[1])
        required_percent = 5.0 - (self.sensitivity - 1) * (4.9 / 99)
        detected = changed_percent >= required_percent

        rising_edge = detected and not self.is_motion
        if detected:
            self.last_motion_at = now
            if rising_edge:
                self.detection_count += 1
            self.is_motion = True
        elif (
            self.is_motion
            and self.last_motion_at is not None
            and now - self.last_motion_at >= self.off_delay
        ):
            self.is_motion = False

        return self.is_motion, rising_edge


def load_options() -> dict[str, Any]:
    try:
        with OPTIONS_PATH.open(encoding="utf-8") as options_file:
            options = json.load(options_file)
    except (OSError, json.JSONDecodeError) as error:
        message = f"Unable to read add-on options at {OPTIONS_PATH}: {error}"
        raise RuntimeError(message) from error

    if not isinstance(options, dict):
        raise RuntimeError("Add-on options must be a JSON object")
    if not options.get("ha_token"):
        raise RuntimeError("Set ha_token in the add-on configuration")
    if not options.get("cameras"):
        raise RuntimeError("Configure at least one camera entity")
    return options


def camera_list(options: dict[str, Any]) -> list[Camera]:
    result: list[Camera] = []
    seen: set[str] = set()
    for item in options["cameras"]:
        entity_id = item["entity_id"].strip()
        if not re.fullmatch(r"camera\.[a-z0-9_]+", entity_id):
            raise RuntimeError(f"Invalid camera entity ID: {entity_id}")
        if entity_id in seen:
            raise RuntimeError(f"Camera entity configured more than once: {entity_id}")
        seen.add(entity_id)
        object_id = re.sub(r"[^a-z0-9_]+", "_", entity_id.removeprefix("camera."))
        result.append(Camera(entity_id, item.get("name") or entity_id, object_id))
    return result


def decode_frame(data: bytes, entity_id: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError(f"Snapshot for {entity_id} exceeds the pixel limit")
            image.load()
            return image.convert("RGB")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"Could not decode snapshot for {entity_id}: {error}") from error


async def fetch_snapshot(
    session: aiohttp.ClientSession, ha_url: str, token: str, entity_id: str
) -> Image.Image:
    url = f"{ha_url.rstrip('/')}/api/camera_proxy/{quote(entity_id, safe='')}"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        if response.content_length is not None and response.content_length > MAX_IMAGE_BYTES:
            raise ValueError(f"Snapshot for {entity_id} exceeds the image-size limit")
        data = await response.content.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"Snapshot for {entity_id} exceeds the image-size limit")
    return decode_frame(data, entity_id)


async def poll_camera(
    session: aiohttp.ClientSession,
    ha_url: str,
    token: str,
    camera: Camera,
) -> Image.Image | None:
    try:
        return await fetch_snapshot(session, ha_url, token, camera.entity_id)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        LOGGER.warning("Camera poll failed for %s: %s", camera.entity_id, error)
        return None


def discovery_documents(camera: Camera) -> tuple[dict[str, Any], dict[str, Any]]:
    base = f"{TOPIC_PREFIX}/{camera.object_id}"
    device = {
        "identifiers": ["pixel_motion_detector"],
        "name": "Pixel Motion Detector",
        "manufacturer": "VideoMotionDetector",
        "model": "Pixel Motion Detector",
        "sw_version": "0.1.0",
    }
    common = {
        "availability_topic": f"{TOPIC_PREFIX}/availability",
        "device": device,
        "origin": {
            "name": "Pixel Motion Detector",
            "sw": "0.1.0",
            "url": "https://github.com/DirectedHunt42/VideoMotionDetector",
        },
    }
    motion = {
        **common,
        "name": f"{camera.name} Motion",
        "unique_id": f"pixel_motion_{camera.object_id}",
        "object_id": f"pixel_motion_{camera.object_id}_motion",
        "state_topic": f"{base}/state",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "motion",
        "icon": "mdi:motion-sensor",
    }
    count = {
        **common,
        "name": f"{camera.name} Motion Events",
        "unique_id": f"pixel_motion_{camera.object_id}_count",
        "object_id": f"pixel_motion_{camera.object_id}_detection_count",
        "state_topic": f"{base}/count",
        "state_class": "total_increasing",
        "icon": "mdi:counter",
    }
    return motion, count


async def publish_discovery(client: aiomqtt.Client, cameras: list[Camera]) -> None:
    for camera in cameras:
        motion, count = discovery_documents(camera)
        for component, config in (("binary_sensor", motion), ("sensor", count)):
            topic = (
                f"{DISCOVERY_PREFIX}/{component}/"
                f"{config['unique_id']}/config"
            )
            await client.publish(topic, json.dumps(config), retain=True)


async def run_monitor(options: dict[str, Any]) -> None:
    cameras = camera_list(options)
    trackers = {
        camera.entity_id: MotionTracker(
            int(options["sensitivity"]),
            int(options["pixel_delta"]),
            int(options["motion_off_delay"]),
        )
        for camera in cameras
    }
    ha_url = str(options["ha_url"])
    token = str(options["ha_token"])
    poll_interval = float(options["poll_interval"])
    timeout = aiohttp.ClientTimeout(total=15)
    published_states = {camera.entity_id: False for camera in cameras}

    while True:
        try:
            async with aiomqtt.Client(
                hostname=str(options["mqtt_host"]),
                port=int(options["mqtt_port"]),
                username=options.get("mqtt_username") or None,
                password=options.get("mqtt_password") or None,
                identifier="pixel-motion-detector",
                will=aiomqtt.Will(
                    topic=f"{TOPIC_PREFIX}/availability",
                    payload="offline",
                    retain=True,
                ),
            ) as client:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await publish_discovery(client, cameras)
                    await client.publish(
                        f"{TOPIC_PREFIX}/availability", "online", retain=True
                    )
                    for camera in cameras:
                        tracker = trackers[camera.entity_id]
                        published_states[camera.entity_id] = False
                        await client.publish(
                            f"{TOPIC_PREFIX}/{camera.object_id}/state",
                            "OFF",
                            retain=True,
                        )
                        await client.publish(
                            f"{TOPIC_PREFIX}/{camera.object_id}/count",
                            str(tracker.detection_count),
                            retain=True,
                        )
                    LOGGER.info("Monitoring %d camera(s)", len(cameras))

                    while True:
                        cycle_started = time.monotonic()
                        frames = await asyncio.gather(
                            *(
                                poll_camera(session, ha_url, token, camera)
                                for camera in cameras
                            )
                        )
                        for camera, frame in zip(cameras, frames):
                            if frame is None:
                                continue
                            tracker = trackers[camera.entity_id]
                            state, rising_edge = tracker.update(
                                frame, time.monotonic()
                            )
                            if rising_edge:
                                LOGGER.info("Motion detected: %s", camera.entity_id)
                                await client.publish(
                                    f"{TOPIC_PREFIX}/{camera.object_id}/count",
                                    str(tracker.detection_count),
                                    retain=True,
                                )
                            if state != published_states[camera.entity_id]:
                                await client.publish(
                                    f"{TOPIC_PREFIX}/{camera.object_id}/state",
                                    "ON" if state else "OFF",
                                    retain=True,
                                )
                                published_states[camera.entity_id] = state
                        elapsed = time.monotonic() - cycle_started
                        await asyncio.sleep(max(0.0, poll_interval - elapsed))
        except aiomqtt.MqttError as error:
            LOGGER.error("MQTT connection failed: %s; retrying in 5 seconds", error)
            await asyncio.sleep(5)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    options = load_options()
    await run_monitor(options)


if __name__ == "__main__":
    asyncio.run(main())
