from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import open3d as o3d

from open3d_reconstruct.configuration import read_json_object
from open3d_reconstruct.paths import DEFAULT_REALSENSE_SENSOR_CONFIG, ROOT
from open3d_reconstruct.realsense import (
    _read_sensor_config,
    _write_intrinsic_and_metadata,
    device_model,
    extract_bag,
    is_supported_device_name,
    load_sensor_config,
)


class RealSenseAdapterTests(unittest.TestCase):
    def test_model_detection(self) -> None:
        self.assertTrue(is_supported_device_name("Intel RealSense D435"))
        self.assertTrue(is_supported_device_name("Intel RealSense D435I"))
        self.assertEqual(device_model("Intel RealSense D435I"), "D435i")
        self.assertFalse(is_supported_device_name("Intel RealSense L515"))

    def test_default_config_uses_only_string_values(self) -> None:
        path, values = _read_sensor_config(DEFAULT_REALSENSE_SENSOR_CONFIG)
        self.assertEqual(path, DEFAULT_REALSENSE_SENSOR_CONFIG.resolve())
        self.assertTrue(values)
        self.assertTrue(all(isinstance(value, str) for value in values.values()))

    def test_device_index_is_resolved_to_serial(self) -> None:
        devices = [
            SimpleNamespace(name="Intel RealSense D435", serial="first"),
            SimpleNamespace(name="Intel RealSense D435I", serial="second"),
        ]
        with mock.patch(
            "open3d_reconstruct.realsense.enumerate_devices", return_value=devices
        ):
            config, selected = load_sensor_config(device=1)
        self.assertIsInstance(config, o3d.t.io.RealSenseSensorConfig)
        self.assertEqual(selected.serial, "second")

    def test_intrinsic_metadata_preserves_depth_scale(self) -> None:
        cache = ROOT / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rs-meta-test-", dir=cache) as name:
            output = Path(name)
            metadata = SimpleNamespace(
                intrinsics=o3d.camera.PinholeCameraIntrinsic(
                    640, 480, 600.0, 600.0, 319.5, 239.5
                ),
                device_name="Intel RealSense D435",
                serial_number="1234",
                color_format="RGB8",
                depth_format="Z16",
                depth_scale=1000.0,
                stream_length_usec=1_000_000,
                width=640,
                height=480,
                fps=30.0,
            )
            _write_intrinsic_and_metadata(o3d, output, metadata)
            value = read_json_object(output / "intrinsic.json")
        self.assertEqual(value["depth_scale"], 1000.0)
        self.assertEqual(value["width"], 640)

    def test_bag_extraction_writes_aligned_dataset_and_manifest(self) -> None:
        color = o3d.geometry.Image(np.full((3, 4, 3), 127, dtype=np.uint8))
        depth = o3d.geometry.Image(np.full((3, 4), 1500, dtype=np.uint16))

        class FakeRGBD:
            def is_empty(self) -> bool:
                return False

            def to_legacy(self):
                return SimpleNamespace(color=color, depth=depth)

        metadata = SimpleNamespace(
            intrinsics=o3d.camera.PinholeCameraIntrinsic(
                4, 3, 4.0, 4.0, 1.5, 1.0
            ),
            device_name="Intel RealSense D435",
            serial_number="test-serial",
            color_format="RGB8",
            depth_format="Z16",
            depth_scale=1000.0,
            stream_length_usec=100_000,
            width=4,
            height=3,
            fps=30.0,
        )

        class FakeReader:
            def __init__(self, _buffer_size: int):
                self.index = 0
                self.frames = [FakeRGBD(), FakeRGBD(), FakeRGBD()]
                self.metadata = metadata
                self.opened = False

            def open(self, _filename: str) -> bool:
                self.opened = True
                return True

            def is_opened(self) -> bool:
                return self.opened

            def is_eof(self) -> bool:
                return self.index >= len(self.frames)

            def next_frame(self):
                frame = self.frames[self.index]
                self.index += 1
                return frame

            def close(self) -> None:
                self.opened = False

        fake_o3d = SimpleNamespace(
            io=o3d.io,
            t=SimpleNamespace(io=SimpleNamespace(RSBagReader=FakeReader)),
        )
        cache = ROOT / ".cache"
        with tempfile.TemporaryDirectory(prefix="rs-bag-test-", dir=cache) as name:
            root = Path(name)
            source = root / "input.bag"
            source.write_bytes(b"fake-bag")
            destination = root / "dataset"
            with mock.patch(
                "open3d_reconstruct.realsense._open3d", return_value=fake_o3d
            ):
                result = extract_bag(source, destination, stride=2)
            manifest = read_json_object(result / ".open3d-reconstruct.json")
            config = read_json_object(result / "config.json")
            color_count = len(list((result / "color").glob("*.jpg")))
            depth_count = len(list((result / "depth").glob("*.png")))
        self.assertEqual(color_count, 2)
        self.assertEqual(depth_count, 2)
        self.assertEqual(manifest["source_frame_count"], 3)
        self.assertEqual(manifest["stride"], 2)
        self.assertEqual(config["depth_scale"], 1000.0)


if __name__ == "__main__":
    unittest.main()
