import unittest

from PIL import Image

from app import Camera, MotionTracker, discovery_documents


def frame(color: int) -> Image.Image:
    return Image.new("L", (160, 90), color)


class MotionTrackerTests(unittest.TestCase):
    def test_initial_frame_does_not_trigger_motion(self) -> None:
        tracker = MotionTracker(sensitivity=60, pixel_delta=25, off_delay=2)

        state, rising_edge = tracker.update(frame(0), now=0)

        self.assertFalse(state)
        self.assertFalse(rising_edge)
        self.assertEqual(tracker.detection_count, 0)

    def test_detects_large_change_and_counts_only_motion_rising_edges(self) -> None:
        tracker = MotionTracker(sensitivity=60, pixel_delta=25, off_delay=2)
        tracker.update(frame(0), now=0)

        state, rising_edge = tracker.update(frame(255), now=1)
        self.assertTrue(state)
        self.assertTrue(rising_edge)
        self.assertEqual(tracker.detection_count, 1)

        state, rising_edge = tracker.update(frame(0), now=2)
        self.assertTrue(state)
        self.assertFalse(rising_edge)
        self.assertEqual(tracker.detection_count, 1)

    def test_motion_clears_after_off_delay(self) -> None:
        tracker = MotionTracker(sensitivity=60, pixel_delta=25, off_delay=2)
        tracker.update(frame(0), now=0)
        tracker.update(frame(255), now=1)

        state, _ = tracker.update(frame(255), now=2.9)
        self.assertTrue(state)
        state, _ = tracker.update(frame(255), now=3)
        self.assertFalse(state)

    def test_sensitivity_controls_required_changed_area(self) -> None:
        low = MotionTracker(sensitivity=1, pixel_delta=25, off_delay=2)
        high = MotionTracker(sensitivity=100, pixel_delta=25, off_delay=2)
        small_change = Image.new("L", (160, 90), 0)
        for y in range(10):
            for x in range(10):
                small_change.putpixel((x, y), 255)
        low.update(frame(0), now=0)
        high.update(frame(0), now=0)

        self.assertFalse(low.update(small_change, now=1)[0])
        self.assertTrue(high.update(small_change, now=1)[0])


class DiscoveryTests(unittest.TestCase):
    def test_discovery_exposes_motion_binary_sensor_and_counter(self) -> None:
        motion, count = discovery_documents(
            Camera("camera.front_door", "Front Door", "front_door")
        )

        self.assertEqual(motion["device_class"], "motion")
        self.assertEqual(motion["state_topic"], "pixel_motion/front_door/state")
        self.assertEqual(count["state_topic"], "pixel_motion/front_door/count")
        self.assertEqual(count["state_class"], "total_increasing")


if __name__ == "__main__":
    unittest.main()
