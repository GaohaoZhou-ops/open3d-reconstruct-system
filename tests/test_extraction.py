from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from open3d_reconstruct.extraction import (
    complete_extraction,
    prepare_extraction,
)
from open3d_reconstruct.paths import ROOT


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
