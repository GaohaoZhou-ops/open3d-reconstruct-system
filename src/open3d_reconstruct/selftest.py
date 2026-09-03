from __future__ import annotations

import tempfile
from pathlib import Path

from .configuration import build_reconstruction_config
from .paths import ROOT
from .pipeline import DEFAULT_STAGES, run_pipeline


def _make_synthetic_dataset(dataset: Path) -> None:
    import numpy as np
    import open3d as o3d

    width, height = 160, 120
    (dataset / "color").mkdir(parents=True)
    (dataset / "depth").mkdir()

    yy, xx = np.mgrid[0:height, 0:width]
    checker = ((xx // 12 + yy // 12) % 2) * 80
    color = np.stack(
        (
            np.clip(xx * 255 / width + checker, 0, 255),
            np.clip(yy * 255 / height + checker, 0, 255),
            np.clip((xx + yy) * 255 / (width + height), 0, 255),
        ),
        axis=-1,
    ).astype(np.uint8)
    depth = (
        1000
        + 55 * np.sin(xx / 15.0)
        + 35 * np.cos(yy / 12.0)
        + 20 * ((xx // 24 + yy // 24) % 2)
    ).astype(np.uint16)

    for index in range(6):
        color_path = dataset / "color" / f"{index:06d}.jpg"
        depth_path = dataset / "depth" / f"{index:06d}.png"
        if not o3d.io.write_image(str(color_path), o3d.geometry.Image(color), 95):
            raise RuntimeError(f"自检彩色图写入失败: {color_path}")
        if not o3d.io.write_image(str(depth_path), o3d.geometry.Image(depth)):
            raise RuntimeError(f"自检深度图写入失败: {depth_path}")

    intrinsic = o3d.camera.PinholeCameraIntrinsic(
        width, height, 145.0, 145.0, (width - 1) / 2, (height - 1) / 2
    )
    if not o3d.io.write_pinhole_camera_intrinsic(
        str(dataset / "intrinsic.json"), intrinsic
    ):
        raise RuntimeError("自检相机内参写入失败")


def run_self_test() -> None:
    cache_dir = ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    print("开始离线自检：创建 6 帧合成 RGB-D 数据并执行四阶段重建。")
    with tempfile.TemporaryDirectory(prefix="self-test-", dir=cache_dir) as temporary:
        dataset = Path(temporary) / "dataset"
        _make_synthetic_dataset(dataset)
        config = build_reconstruction_config(
            dataset,
            dataset / "intrinsic.json",
            overrides=(
                "n_frames_per_fragment=3",
                "n_keyframes_per_n_frame=5",
                "depth_max=2.0",
                "voxel_size=0.04",
                "tsdf_cubic_size=2.0",
                'icp_method="point_to_plane"',
            ),
        )
        mesh = run_pipeline(config, DEFAULT_STAGES)
        if mesh is None or not mesh.is_file():
            raise RuntimeError("离线自检没有生成最终网格")
    print("离线自检通过：Open3D 四阶段重建链路工作正常。")
