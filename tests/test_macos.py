from __future__ import annotations

import contextlib
import io
import plistlib
import subprocess
import unittest
from unittest import mock

from open3d_reconstruct.doctor import _find_macos_usb_devices
from open3d_reconstruct.permissions import install_udev_rules


class MacOSPlatformTests(unittest.TestCase):
    def test_ioreg_usb_tree_is_parsed_recursively(self) -> None:
        tree = [
            {
                "USB Product Name": "Hub",
                "IORegistryEntryChildren": [
                    {
                        "USB Product Name": "Intel RealSense D435i",
                        "idVendor": 0x8086,
                        "idProduct": 0x0B3A,
                        "UsbLinkSpeed": 5_000_000_000,
                    }
                ],
            }
        ]
        completed = subprocess.CompletedProcess(
            args=["ioreg"],
            returncode=0,
            stdout=plistlib.dumps(tree),
            stderr=b"",
        )
        with (
            mock.patch(
                "open3d_reconstruct.doctor.shutil.which",
                return_value="/usr/sbin/ioreg",
            ),
            mock.patch(
                "open3d_reconstruct.doctor.subprocess.run",
                return_value=completed,
            ),
        ):
            devices = _find_macos_usb_devices(vendor="8086", products={"0b3a"})

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["product"], "0b3a")
        self.assertEqual(devices[0]["name"], "Intel RealSense D435i")
        self.assertEqual(devices[0]["speed"], 5000.0)
        self.assertIsNone(devices[0]["node"])

    def test_udev_command_is_a_noop_on_macos(self) -> None:
        output = io.StringIO()
        with (
            mock.patch("open3d_reconstruct.permissions.sys.platform", "darwin"),
            mock.patch("open3d_reconstruct.permissions.subprocess.run") as run,
            contextlib.redirect_stdout(output),
        ):
            install_udev_rules("all")

        run.assert_not_called()
        self.assertIn("macOS 不使用 udev", output.getvalue())


if __name__ == "__main__":
    unittest.main()
