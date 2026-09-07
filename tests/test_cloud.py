from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from open3d_reconstruct.cloud import (
    dataset_target,
    reconstruction_arguments,
    run_cloud_wizard,
)
from open3d_reconstruct.configuration import load_reconstruction_plan
from open3d_reconstruct.paths import DEFAULT_RECONSTRUCTION_PROFILES


class CloudWizardTests(unittest.TestCase):
    def test_recording_output_is_isolated_by_quality_profile(self) -> None:
        plan = load_reconstruction_plan(DEFAULT_RECONSTRUCTION_PROFILES, "high")
        source = Path("/tmp/example.mkv")
        target = dataset_target(source, plan)
        assert target is not None
        self.assertEqual(target.name, "example-high")
        arguments = reconstruction_arguments(source, plan, target)
        self.assertIn("--profile", arguments)
        self.assertIn("high", arguments)
        self.assertIn("--dataset", arguments)

    def test_noninteractive_options_still_show_summary_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sample.mkv"
            source.write_bytes(b"fake")
            output = io.StringIO()
            captured: list[list[str]] = []

            result = run_cloud_wizard(
                source=source,
                config_path=DEFAULT_RECONSTRUCTION_PROFILES,
                profile="low",
                assume_yes=True,
                output=output,
                runner=lambda arguments: captured.append(arguments) or 0,
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(captured), 1)
        self.assertIn("低质量", output.getvalue())
        self.assertIn("每 4 帧取 1 帧", output.getvalue())

    def test_confirmation_can_cancel_without_starting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sample.bag"
            source.write_bytes(b"fake")
            answers = iter(("2", "n"))
            called = False

            def runner(_arguments: list[str]) -> int:
                nonlocal called
                called = True
                return 0

            result = run_cloud_wizard(
                source=source,
                output=io.StringIO(),
                input_fn=lambda _prompt: next(answers),
                runner=runner,
            )

        self.assertEqual(result, 0)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
