from __future__ import annotations

import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from open3d_reconstruct.k4a_live import (
    K4ADeviceConfiguration,
    K4AImuSample,
    K4APreviewWorker,
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

    def test_existing_camera_jpeg_is_published_without_reencoding(self) -> None:
        import cv2

        with tempfile.TemporaryDirectory() as temporary:
            publisher = LivePreviewPublisher(
                Path(temporary), hardware="azure-kinect", max_fps=15
            )
            color = np.full((12, 16, 3), 120, dtype=np.uint8)
            encoded_ok, encoded = cv2.imencode(".jpg", color)
            self.assertTrue(encoded_ok)
            jpeg = encoded.tobytes()
            depth = np.full((8, 10), 900, dtype=np.uint16)

            self.assertTrue(
                publisher.publish_jpeg_depth(
                    jpeg,
                    depth,
                    frame_count=3,
                    color_width=16,
                    color_height=12,
                    force=True,
                )
            )
            self.assertEqual((publisher.directory / "rgb.jpg").read_bytes(), jpeg)
            state = json.loads((publisher.directory / "state.json").read_text())
            self.assertTrue(state["rgb"]["passthrough"])
            self.assertEqual(state["preview_target_fps"], 15)
            self.assertGreaterEqual(state["preview_processing_ms"], 0)

    def test_k4a_preview_worker_throttles_and_balances_references(self) -> None:
        class FakeCore:
            def __init__(self) -> None:
                self.references = 0
                self.releases = 0

            def k4a_capture_reference(self, _capture) -> None:
                self.references += 1

            def k4a_capture_release(self, _capture) -> None:
                self.releases += 1

        class FakeApi:
            def __init__(self) -> None:
                self.core = FakeCore()

        with tempfile.TemporaryDirectory() as temporary:
            publisher = LivePreviewPublisher(
                Path(temporary), hardware="azure-kinect", max_fps=20
            )
            api = FakeApi()
            with mock.patch(
                "open3d_reconstruct.k4a_live._preview_capture"
            ) as preview, mock.patch(
                "open3d_reconstruct.k4a_live.time.monotonic",
                side_effect=(0.0, 0.034, 0.069, 0.104, 0.138, 0.173),
            ):
                worker = K4APreviewWorker(api, publisher)
                for frame_count in range(1, 7):
                    worker.submit(ctypes.c_void_p(frame_count), frame_count)
                worker.close()

            self.assertGreaterEqual(preview.call_count, 1)
            self.assertEqual(api.core.references, 4)
            self.assertEqual(api.core.releases, 4)

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
