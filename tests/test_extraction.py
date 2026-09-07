from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from open3d_reconstruct.extraction import (
    complete_extraction,
    prepare_extraction,
)
from open3d_reconstruct.paths import ROOT


def _calibration_camera(
    purpose: str,
    *,
    width: int,
    height: int,
    parameters: list[float] | None = None,
) -> dict:
    return {
        "Purpose": purpose,
        "SensorWidth": width,
        "SensorHeight": height,
        "MetricRadius": 1.7,
        "Intrinsics": {
            "ModelType": "CALIBRATION_LensDistortionModelBrownConrady",
            "ModelParameters": parameters
            or [0.5, 0.5, 0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        },
        "Rt": {
            "Rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            "Translation": [0, 0, 0],
        },
    }


class PortableMKVCalibrationTests(unittest.TestCase):
    def test_mode_specific_intrinsics_apply_k4a_crop_and_binning(self) -> None:
        import numpy as np

        from open3d_reconstruct.mkv_portable import mode_specific_calibration

        depth = mode_specific_calibration(
            _calibration_camera("CALIBRATION_CameraPurposeDepth", width=1024, height=1024),
            width=512,
            height=512,
            kind="depth",
        )
        color = mode_specific_calibration(
            _calibration_camera(
                "CALIBRATION_CameraPurposePhotoVideo", width=4096, height=3072
            ),
            width=1280,
            height=720,
            kind="color",
        )

        np.testing.assert_allclose(
            depth.matrix,
            [[256.0, 0.0, 255.5], [0.0, 256.0, 255.5], [0.0, 0.0, 1.0]],
        )
        np.testing.assert_allclose(
            color.matrix,
            [[640.0, 0.0, 639.5], [0.0, 480.0, 359.5], [0.0, 0.0, 1.0]],
        )

    def test_probe_combines_container_and_track_metadata(self) -> None:
        from open3d_reconstruct.mkv_portable import probe_mkv

        probe_value = {
            "streams": [
                {
                    "index": 0,
                    "width": 1280,
                    "height": 720,
                    "tags": {"title": "COLOR", "K4A_COLOR_MODE": "MJPG_720P"},
                },
                {
                    "index": 1,
                    "width": 512,
                    "height": 512,
                    "tags": {"title": "DEPTH", "K4A_DEPTH_MODE": "WFOV_2X2BINNED"},
                },
            ],
            "format": {"tags": {"K4A_DEVICE_SERIAL_NUMBER": "serial"}},
        }
        with mock.patch(
            "open3d_reconstruct.mkv_portable._run_json", return_value=probe_value
        ):
            result = probe_mkv(Path("recording.mkv"), "ffprobe")

        self.assertEqual(result.color.index, 0)
        self.assertEqual(result.depth.index, 1)
        self.assertEqual(result.tags["K4A_COLOR_MODE"], "MJPG_720P")
        self.assertEqual(result.tags["K4A_DEPTH_MODE"], "WFOV_2X2BINNED")
        self.assertEqual(result.tags["K4A_DEVICE_SERIAL_NUMBER"], "serial")

    def test_probe_can_use_bundled_ffmpeg_without_ffprobe(self) -> None:
        from open3d_reconstruct.mkv_portable import probe_mkv

        completed = subprocess.CompletedProcess(
            args=["bundled-ffmpeg"],
            returncode=1,
            stdout="",
            stderr="""
Input #0, matroska,webm, from 'recording.mkv':
  Metadata:
    K4A_DEVICE_SERIAL_NUMBER: serial
  Stream #0:0(eng): Video: mjpeg, yuvj422p, 1280x720, 30 fps
      Metadata:
        title           : COLOR
        K4A_COLOR_MODE  : MJPG_720P
  Stream #0:1(eng): Video: rawvideo, gray16be, 512x512, 30 fps
      Metadata:
        title           : DEPTH
        K4A_DEPTH_MODE  : WFOV_2X2BINNED
""",
        )
        with mock.patch(
            "open3d_reconstruct.mkv_portable.subprocess.run",
            return_value=completed,
        ) as run:
            result = probe_mkv(
                Path("recording.mkv"), None, ffmpeg="bundled-ffmpeg"
            )

        self.assertEqual(
            (result.color.index, result.color.width, result.color.height),
            (0, 1280, 720),
        )
        self.assertEqual(
            (result.depth.index, result.depth.width, result.depth.height),
            (1, 512, 512),
        )
        self.assertEqual(result.tags["K4A_DEVICE_SERIAL_NUMBER"], "serial")
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_rational6kt_uses_the_k4a_tangential_convention(self) -> None:
        import numpy as np

        from open3d_reconstruct.mkv_portable import (
            _project_normalized,
            mode_specific_calibration,
        )

        parameters = [
            0.5,
            0.5,
            0.5,
            0.5,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0.01,
            0.02,
        ]
        camera_json = _calibration_camera(
            "CALIBRATION_CameraPurposeDepth",
            width=1024,
            height=1024,
            parameters=parameters,
        )
        camera_json["Intrinsics"]["ModelType"] = (
            "CALIBRATION_LensDistortionModelRational6KT"
        )
        camera = mode_specific_calibration(
            camera_json, width=512, height=512, kind="depth"
        )

        projected_x, projected_y, valid = _project_normalized(
            camera, np.array([0.1]), np.array([0.2])
        )

        self.assertTrue(bool(valid[0]))
        # K4A's Rational6KT model uses x*y*p1/p2, whereas Brown-Conrady
        # and OpenCV use 2*x*y*p1/p2.
        self.assertAlmostEqual(float(projected_x[0]), 0.1011 * 256 + 255.5)
        self.assertAlmostEqual(float(projected_y[0]), 0.2028 * 256 + 255.5)

    def test_identity_calibration_produces_matching_rgbd_shapes(self) -> None:
        import numpy as np

        from open3d_reconstruct.mkv_portable import RGBDAligner, mode_specific_calibration

        depth = mode_specific_calibration(
            _calibration_camera("CALIBRATION_CameraPurposeDepth", width=1024, height=1024),
            width=512,
            height=512,
            kind="depth",
        )
        color = mode_specific_calibration(
            _calibration_camera(
                "CALIBRATION_CameraPurposePhotoVideo", width=4096, height=3072
            ),
            width=1280,
            height=720,
            kind="color",
        )
        aligner = RGBDAligner(depth, color)
        source_color = np.full((720, 1280, 3), [12, 34, 56], dtype=np.uint8)
        source_depth = np.full((512, 512), 1000, dtype=np.uint16)

        aligned_color, aligned_depth = aligner.align(source_color, source_depth)

        self.assertEqual(aligned_color.shape, (512, 512, 3))
        self.assertEqual(aligned_depth.shape, (512, 512))
        np.testing.assert_array_equal(aligned_color[256, 256], [12, 34, 56])
        self.assertEqual(int(aligned_depth[256, 256]), 1000)


class AzureExtractionRoutingTests(unittest.TestCase):
    def test_wsl_uses_portable_calibrated_mkv_extractor(self) -> None:
        from open3d_reconstruct import azure

        source = Path("recording.mkv")
        destination = Path("dataset")
        with (
            mock.patch.object(azure, "IS_WSL", True),
            mock.patch.object(azure, "K4A_LIVE_SUPPORTED", True),
            mock.patch(
                "open3d_reconstruct.mkv_portable.extract_mkv_portable",
                return_value=destination,
            ) as portable,
        ):
            result = azure.extract_mkv(
                source, destination, force=True, stride=2
            )

        self.assertEqual(result, destination)
        portable.assert_called_once_with(
            source, destination, force=True, stride=2
        )

    def test_headless_linux_uses_portable_calibrated_mkv_extractor(self) -> None:
        from open3d_reconstruct import azure

        source = Path("recording.mkv")
        destination = Path("dataset")
        with (
            mock.patch.object(azure, "SYSTEM", "Linux"),
            mock.patch.object(azure, "IS_WSL", False),
            mock.patch.object(azure, "K4A_LIVE_SUPPORTED", True),
            mock.patch.dict(
                "open3d_reconstruct.azure.os.environ",
                {"DISPLAY": "", "WAYLAND_DISPLAY": ""},
                clear=False,
            ),
            mock.patch(
                "open3d_reconstruct.mkv_portable.extract_mkv_portable",
                return_value=destination,
            ) as portable,
        ):
            result = azure.extract_mkv(source, destination, stride=4)

        self.assertEqual(result, destination)
        portable.assert_called_once_with(
            source, destination, force=False, stride=4
        )


class ExtractionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        cache = ROOT / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="extraction-test-", dir=cache
        )
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.bag"
        self.source.write_bytes(b"test-bag-fingerprint")
        self.destination = self.root / "dataset"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _complete(self, stride: int = 1) -> None:
        workspace = prepare_extraction(
            self.source,
            self.destination,
            suffix=".bag",
            format_name="RealSense BAG",
            force=False,
            stride=stride,
        )
        assert workspace.partial is not None
        (workspace.partial / "color").mkdir()
        (workspace.partial / "depth").mkdir()
        (workspace.partial / "color" / "000000.jpg").write_bytes(b"color")
        (workspace.partial / "depth" / "000000.png").write_bytes(b"depth")
        (workspace.partial / "intrinsic.json").write_text("{}", encoding="utf-8")
        complete_extraction(
            workspace,
            frame_count=1,
            source_frame_count=stride,
            depth_scale=1000.0,
        )

    def test_complete_extraction_is_reused_for_same_source_and_stride(self) -> None:
        self._complete(stride=2)
        workspace = prepare_extraction(
            self.source,
            self.destination,
            suffix=".bag",
            format_name="RealSense BAG",
            force=False,
            stride=2,
        )
        self.assertTrue(workspace.reused)

    def test_different_stride_is_not_silently_reused(self) -> None:
        self._complete(stride=2)
        with self.assertRaises(FileExistsError):
            prepare_extraction(
                self.source,
                self.destination,
                suffix=".bag",
                format_name="RealSense BAG",
                force=False,
                stride=1,
            )

    def test_force_refuses_unmanaged_directory(self) -> None:
        self.destination.mkdir()
        (self.destination / "user-file.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_extraction(
                self.source,
                self.destination,
                suffix=".bag",
                format_name="RealSense BAG",
                force=True,
                stride=1,
            )
        self.assertTrue((self.destination / "user-file.txt").is_file())

    def test_input_cannot_be_inside_recovery_directory(self) -> None:
        partial = self.destination.with_name(self.destination.name + ".extracting")
        partial.mkdir()
        source = partial / "input.bag"
        source.write_bytes(b"keep-source")
        with self.assertRaises(ValueError):
            prepare_extraction(
                source,
                self.destination,
                suffix=".bag",
                format_name="RealSense BAG",
                force=False,
                stride=1,
            )
        self.assertTrue(source.is_file())


if __name__ == "__main__":
    unittest.main()
