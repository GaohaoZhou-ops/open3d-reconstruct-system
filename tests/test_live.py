from __future__ import annotations

import ctypes
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from open3d_reconstruct.k4a_live import (
    K4ADeviceConfiguration,
    K4AImuSample,
    native_configuration,
)
from open3d_reconstruct.live import LivePreviewPublisher


class LivePreviewTests(unittest.TestCase):
    def test_rgb_depth_and_imu_are_published_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            publisher = LivePreviewPublisher(
                directory, hardware="azure-kinect", max_fps=30
            )
            publisher.update_imu(
                acceleration=(0.1, 9.8, -0.2),
                gyroscope=(0.01, -0.02, 0.03),
                temperature=31.5,
                timestamp_usec=123456,
                sample_rate_hz=1600.0,
                sample_count=320,
            )
            color = np.zeros((12, 16, 3), dtype=np.uint8)
            color[..., 0] = 220
            depth = np.full((8, 10), 1250, dtype=np.uint16)
            depth[0, 0] = 0

            self.assertTrue(
                publisher.publish_arrays(color, depth, frame_count=7, force=True)
            )
            state = json.loads((directory / "state.json").read_text())
            self.assertEqual(state["sequence"], 1)
            self.assertEqual(state["frame_count"], 7)
            self.assertTrue(state["imu"]["available"])
            self.assertEqual(state["imu"]["sample_count"], 320)
            self.assertGreater(state["depth"]["valid_ratio"], 0.9)
            self.assertTrue((directory / "rgb.jpg").read_bytes().startswith(b"\xff\xd8"))
            self.assertTrue((directory / "depth.jpg").read_bytes().startswith(b"\xff\xd8"))
            self.assertFalse(list(directory.glob("*.tmp")))

            publisher.close(frame_count=7)
            closed = json.loads((directory / "state.json").read_text())
            self.assertFalse(closed["active"])

    def test_realsense_imu_capability_is_reported_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            d435 = LivePreviewPublisher(Path(temporary) / "d435", hardware="d435")
            d435i = LivePreviewPublisher(Path(temporary) / "d435i", hardware="d435i")
            d435_state = json.loads((d435.directory / "state.json").read_text())
            d435i_state = json.loads((d435i.directory / "state.json").read_text())
            self.assertFalse(d435_state["imu"]["supported"])
            self.assertTrue(d435i_state["imu"]["supported"])
            self.assertFalse(d435i_state["imu"]["available"])


class K4AConfigurationTests(unittest.TestCase):
    def test_default_configuration_has_expected_native_layout(self) -> None:
        config = native_configuration()
        self.assertEqual(ctypes.sizeof(K4ADeviceConfiguration), 36)
        self.assertEqual(ctypes.sizeof(K4AImuSample), 48)
        self.assertEqual(config.color_format, 0)
        self.assertEqual(config.color_resolution, 1)
        self.assertEqual(config.depth_mode, 3)
        self.assertEqual(config.camera_fps, 2)
        self.assertTrue(config.synchronized_images_only)


if __name__ == "__main__":
    unittest.main()
