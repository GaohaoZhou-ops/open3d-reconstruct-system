from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from open3d_reconstruct.compute import (
    normalize_compute_backend,
    resolve_compute_backend,
)


class _FakeCUDA:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available

    @staticmethod
    def current_device() -> int:
        return 0

    @staticmethod
    def get_device_name(_index: int) -> str:
        return "Test CUDA GPU"


class _FakeMPS:
    def __init__(self, available: bool) -> None:
        self.available = available

    @staticmethod
    def is_built() -> bool:
        return True

    def is_available(self) -> bool:
        return self.available


class _FakeBackends:
    def __init__(self, mps_available: bool) -> None:
        self.mps = _FakeMPS(mps_available)


class _FakeTorch:
    __version__ = "test"

    def __init__(self, *, cuda: bool, mps: bool) -> None:
        self.cuda = _FakeCUDA(cuda)
        self.backends = _FakeBackends(mps)


class ComputeSelectionTests(unittest.TestCase):
    def test_backend_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_compute_backend("CPU:0"), "cpu")
        self.assertEqual(normalize_compute_backend("CUDA:0"), "cuda")
        self.assertEqual(normalize_compute_backend("metal"), "mps")
        with self.assertRaises(ValueError):
            normalize_compute_backend("vulkan")

    def test_auto_prefers_mps_on_macos(self) -> None:
        result = resolve_compute_backend(
            "auto",
            torch_module=_FakeTorch(cuda=True, mps=True),
            platform_name="darwin",
        )
        self.assertEqual(result.backend, "mps")
        self.assertTrue(result.accelerated)

    def test_auto_prefers_cuda_on_linux(self) -> None:
        result = resolve_compute_backend(
            "auto",
            torch_module=_FakeTorch(cuda=True, mps=False),
            platform_name="linux",
        )
        self.assertEqual(result.backend, "cuda")
        self.assertTrue(result.accelerated)

    def test_missing_gpu_falls_back_to_cpu(self) -> None:
        result = resolve_compute_backend(
            "auto",
            torch_module=_FakeTorch(cuda=False, mps=False),
            platform_name="linux",
        )
        self.assertEqual(result.backend, "cpu")
        self.assertFalse(result.accelerated)
        self.assertIn("回退", result.detail)

    def test_explicit_unavailable_backend_also_falls_back(self) -> None:
        result = resolve_compute_backend(
            "cuda",
            torch_module=_FakeTorch(cuda=False, mps=True),
            platform_name="darwin",
        )
        self.assertEqual(result.backend, "cpu")
        self.assertFalse(result.accelerated)
        self.assertIn("CUDA 不可用", result.detail)

    def test_gpu_runtime_failure_is_cached_as_cpu_fallback(self) -> None:
        from open3d_reconstruct import torch_odometry

        config = {
            "compute_backend_resolved": "cuda",
            "compute_accelerated": True,
        }
        torch_odometry.reset_gpu_odometry_state()
        with patch.object(
            torch_odometry,
            "TorchRGBDOdometry",
            side_effect=RuntimeError("test GPU failure"),
        ) as constructor:
            first, first_error = torch_odometry.try_gpu_rgbd_odometry(
                "source-color",
                "source-depth",
                "target-color",
                "target-depth",
                object(),
                config,
            )
            second, second_error = torch_odometry.try_gpu_rgbd_odometry(
                "source-color",
                "source-depth",
                "target-color",
                "target-depth",
                object(),
                config,
            )
        torch_odometry.reset_gpu_odometry_state()

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIn("test GPU failure", first_error or "")
        self.assertEqual(first_error, second_error)
        constructor.assert_called_once_with("cuda")


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch 未安装")
class TorchOdometryTests(unittest.TestCase):
    def test_identical_rgbd_pair_stays_at_identity(self) -> None:
        import cv2
        import open3d as o3d

        from open3d_reconstruct.torch_odometry import TorchRGBDOdometry

        width, height = 80, 64
        yy, xx = np.mgrid[0:height, 0:width]
        color = np.stack(
            (
                (xx * 3 + yy * 2) % 255,
                (xx * 7 + yy) % 255,
                (xx + yy * 5) % 255,
            ),
            axis=-1,
        ).astype(np.uint8)
        depth = (
            900.0 + xx * 1.5 + yy * 0.8 + 20.0 * np.sin(xx / 8.0)
        ).astype(np.uint16)
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            width, height, 75.0, 75.0, (width - 1) / 2, (height - 1) / 2
        )
        config = {
            "depth_scale": 1000.0,
            "depth_min": 0.2,
            "depth_max": 3.0,
            "depth_diff_max": 0.07,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            color_path = root / "color.png"
            depth_path = root / "depth.png"
            self.assertTrue(cv2.imwrite(str(color_path), color))
            self.assertTrue(cv2.imwrite(str(depth_path), depth))
            result = TorchRGBDOdometry("cpu", cache_size=2).estimate(
                str(color_path),
                str(depth_path),
                str(color_path),
                str(depth_path),
                intrinsic,
                config,
            )

        self.assertTrue(result.success, result.detail)
        np.testing.assert_allclose(result.transformation, np.eye(4), atol=1e-4)
        self.assertGreater(result.correspondences, 100)
        self.assertTrue(np.isfinite(result.information).all())


if __name__ == "__main__":
    unittest.main()
