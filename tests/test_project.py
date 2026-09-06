from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from open3d_reconstruct.configuration import write_json
from open3d_reconstruct.project import (
    PROJECT_FILENAME,
    PROJECT_KIND,
    load_reconstruction_project,
    write_reconstruction_project,
)


class ReconstructionProjectTests(unittest.TestCase):
    @staticmethod
    def make_dataset(root: Path) -> Path:
        dataset = root / "可移动工程"
        (dataset / "scene").mkdir(parents=True)
        (dataset / "scene" / "integrated.ply").write_bytes(b"ply\nmesh")
        (dataset / "scene" / "trajectory.log").write_text("trajectory\n")
        write_json(dataset / "intrinsic.json", {"width": 640, "height": 480})
        write_json(
            dataset / "run-report.json",
            {
                "frame_count": 42,
                "stages": ["make", "register", "refine", "integrate"],
                "timings_seconds": {"make": 8.5, "integrate": 2.0},
                "total_seconds": 10.5,
                "compute": {"backend": "cpu", "device": "cpu"},
                "mesh": {"vertices": 12, "triangles": 10, "bytes": 8},
            },
        )
        return dataset

    def test_project_records_portable_artifacts_and_web_analysis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="open3d-project-test-") as temporary:
            root = Path(temporary)
            dataset = self.make_dataset(root)
            recording = root / "recording.mkv"
            recording.write_bytes(b"recording")
            manifest = write_reconstruction_project(
                dataset,
                recording=recording,
                hardware="azure-kinect",
                started_at="2026-09-06T01:00:00+08:00",
                finished_at="2026-09-06T01:01:00+08:00",
                settings={"voxel_size": 0.03},
                conversion={
                    "status": "completed",
                    "frame_count": 42,
                    "pause_count": 1,
                    "paused_total_seconds": 3.5,
                    "matching": {
                        "attempted": 2,
                        "succeeded": 1,
                        "failed": 1,
                        "events": [{"source": 0, "target": 1, "success": True}],
                    },
                },
                logs=[{"time": "01:00:01", "level": "info", "message": "开始"}],
            )

            self.assertEqual(manifest["kind"], PROJECT_KIND)
            self.assertEqual(manifest["artifacts"]["mesh"], "scene/integrated.ply")
            self.assertNotIn(str(dataset), json.dumps(manifest["artifacts"]))
            self.assertEqual(manifest["web_analysis"]["matching"]["attempted"], 2)
            loaded = load_reconstruction_project(dataset)
            self.assertEqual(loaded["mesh"], (dataset / "scene" / "integrated.ply").resolve())
            self.assertEqual(loaded["recording"], recording.resolve())
            self.assertEqual(loaded["reconstruction"]["frame_count"], 42)

    def test_legacy_completed_dataset_is_upgraded_on_open(self) -> None:
        with tempfile.TemporaryDirectory(prefix="open3d-legacy-project-") as temporary:
            dataset = self.make_dataset(Path(temporary))
            loaded = load_reconstruction_project(dataset)
            self.assertTrue((dataset / PROJECT_FILENAME).is_file())
            self.assertEqual(loaded["manifest"]["kind"], PROJECT_KIND)
            self.assertEqual(loaded["mesh"].name, "integrated.ply")

    def test_artifact_path_cannot_escape_project_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="open3d-project-safety-") as temporary:
            root = Path(temporary)
            dataset = self.make_dataset(root)
            outside = root / "outside.ply"
            outside.write_bytes(b"ply\noutside")
            write_json(
                dataset / PROJECT_FILENAME,
                {
                    "kind": PROJECT_KIND,
                    "format_version": 1,
                    "artifacts": {"mesh": "../outside.ply"},
                    "reconstruction": {},
                },
            )
            with self.assertRaisesRegex(ValueError, "超出工程目录"):
                load_reconstruction_project(dataset)

    def test_invalid_analysis_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="open3d-project-invalid-") as temporary:
            dataset = self.make_dataset(Path(temporary))
            write_json(
                dataset / PROJECT_FILENAME,
                {
                    "kind": PROJECT_KIND,
                    "format_version": 1,
                    "artifacts": {"mesh": "scene/integrated.ply"},
                    "reconstruction": {},
                    "web_analysis": {"matching": {"events": "invalid"}},
                },
            )
            with self.assertRaisesRegex(ValueError, "events 必须是数组"):
                load_reconstruction_project(dataset)


if __name__ == "__main__":
    unittest.main()
