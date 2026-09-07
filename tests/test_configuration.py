from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from open3d_reconstruct.configuration import (
    RECONSTRUCTION_PARAMETER_RULES,
    build_reconstruction_config,
    load_reconstruction_plan,
    reconstruction_profile_catalog,
)
from open3d_reconstruct.paths import (
    DEFAULT_RECONSTRUCTION_CONFIG,
    DEFAULT_RECONSTRUCTION_PROFILES,
)


class ReconstructionYamlTests(unittest.TestCase):
    def test_portable_catalog_contains_three_complete_quality_levels(self) -> None:
        catalog = reconstruction_profile_catalog(DEFAULT_RECONSTRUCTION_PROFILES)
        self.assertEqual(catalog["default_profile"], "medium")
        self.assertEqual(list(catalog["profiles"]), ["low", "medium", "high"])
        self.assertEqual(catalog["profiles"]["low"]["stride"], 4)
        self.assertEqual(catalog["profiles"]["medium"]["stride"], 2)
        self.assertEqual(catalog["profiles"]["high"]["stride"], 1)
        for profile in catalog["profiles"].values():
            self.assertEqual(
                set(profile["parameters"]), set(RECONSTRUCTION_PARAMETER_RULES)
            )

    def test_selected_profile_populates_effective_config(self) -> None:
        plan = load_reconstruction_plan(DEFAULT_RECONSTRUCTION_PROFILES, "high")
        with tempfile.TemporaryDirectory() as temporary:
            dataset = Path(temporary)
            intrinsic = dataset / "intrinsic.json"
            config = build_reconstruction_config(
                dataset,
                intrinsic,
                DEFAULT_RECONSTRUCTION_PROFILES,
                plan=plan,
            )
        self.assertEqual(config["voxel_size"], 0.03)
        self.assertEqual(config["n_keyframes_per_n_frame"], 3)
        self.assertEqual(config["compute_backend"], "auto")

    def test_dataset_depth_scale_still_overrides_legacy_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset = Path(temporary)
            (dataset / "config.json").write_text(
                '{"depth_scale": 4000.0}\n', encoding="utf-8"
            )
            config = build_reconstruction_config(
                dataset,
                dataset / "intrinsic.json",
                DEFAULT_RECONSTRUCTION_CONFIG,
            )
        self.assertEqual(config["depth_scale"], 4000.0)

    def test_invalid_profile_and_unsafe_yaml_tag_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "配置档位不存在"):
            load_reconstruction_plan(DEFAULT_RECONSTRUCTION_PROFILES, "ultra")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unsafe.yaml"
            path.write_text("value: !!python/object/apply:os.system ['true']\n")
            with self.assertRaisesRegex(ValueError, "不是有效 YAML"):
                load_reconstruction_plan(path)


if __name__ == "__main__":
    unittest.main()
