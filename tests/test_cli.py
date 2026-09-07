from __future__ import annotations

import unittest

from open3d_reconstruct.cli import build_parser


class CliCameraTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_live_commands_keep_azure_as_compatible_default(self) -> None:
        args = self.parser.parse_args(["preview", "--no-window"])
        self.assertEqual(args.camera, "azure-kinect")

    def test_d435_alias_selects_realsense(self) -> None:
        args = self.parser.parse_args(
            ["record", "--camera", "d435", "--seconds", "1", "--no-preview"]
        )
        self.assertEqual(args.camera, "realsense")

    def test_d435i_alias_selects_realsense(self) -> None:
        args = self.parser.parse_args(["scan", "--camera", "d435i"])
        self.assertEqual(args.camera, "realsense")

    def test_doctor_checks_all_backends_by_default(self) -> None:
        args = self.parser.parse_args(["doctor"])
        self.assertEqual(args.camera, "all")

    def test_web_uses_local_single_port_default(self) -> None:
        args = self.parser.parse_args(["web", "--no-browser"])
        self.assertEqual(args.port, 11920)
        self.assertTrue(args.no_browser)

    def test_service_commands_are_explicit(self) -> None:
        start = self.parser.parse_args(["service", "start"])
        status = self.parser.parse_args(["service", "status", "--quiet"])
        stop = self.parser.parse_args(["service", "stop"])
        self.assertEqual(start.service_command, "start")
        self.assertEqual(status.service_command, "status")
        self.assertTrue(status.quiet)
        self.assertEqual(stop.service_command, "stop")

    def test_reconstruction_uses_automatic_compute_backend(self) -> None:
        automatic = self.parser.parse_args(["reconstruct", "dataset"])
        cpu = self.parser.parse_args(
            ["reconstruct", "dataset", "--compute-backend", "cpu"]
        )
        self.assertIsNone(automatic.compute_backend)
        self.assertEqual(cpu.compute_backend, "cpu")

    def test_yaml_profile_and_cloud_commands_are_available(self) -> None:
        reconstruction = self.parser.parse_args(
            [
                "reconstruct",
                "recording.mkv",
                "--reconstruction-config",
                "config/reconstruction.yaml",
                "--profile",
                "high",
            ]
        )
        wizard = self.parser.parse_args(["wizard", "--profile", "low", "--yes"])
        compute = self.parser.parse_args(["gpu-info"])
        self.assertEqual(reconstruction.profile, "high")
        self.assertIsNone(reconstruction.stride)
        self.assertEqual(wizard.profile, "low")
        self.assertTrue(wizard.yes)
        self.assertEqual(compute.command, "gpu-info")


if __name__ == "__main__":
    unittest.main()
