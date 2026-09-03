from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from .configuration import write_json
from .paths import VENDOR_RECONSTRUCTION_DIR


STAGES = ("make", "register", "refine", "integrate", "slac", "slac-integrate")
DEFAULT_STAGES = ("make", "register", "refine", "integrate")
STAGE_MODULES = {
    "make": "make_fragments",
    "register": "register_fragments",
    "refine": "refine_registration",
    "integrate": "integrate_scene",
    "slac": "slac",
    "slac-integrate": "slac_integrate",
}
STAGE_LABELS = {
    "make": "生成局部片段",
    "register": "全局片段配准",
    "refine": "精细配准",
    "integrate": "TSDF 场景融合",
    "slac": "SLAC 非刚性优化",
    "slac-integrate": "SLAC 融合",
}


def parse_stages(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_STAGES
    requested = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not requested:
        raise ValueError("--stages 不能为空")
    unknown = [item for item in requested if item not in STAGES]
    if unknown:
        raise ValueError(f"未知重建阶段: {', '.join(unknown)}；可用值: {', '.join(STAGES)}")
    if len(set(requested)) != len(requested):
        raise ValueError("--stages 中不能重复指定阶段")
    order = [STAGES.index(item) for item in requested]
    if order != sorted(order):
        raise ValueError(f"重建阶段必须按以下顺序排列: {', '.join(STAGES)}")
    return requested


def _add_vendor_path() -> None:
    path = str(VENDOR_RECONSTRUCTION_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def initialize_upstream_config(config: dict[str, Any]) -> dict[str, Any]:
    _add_vendor_path()
    module = importlib.import_module("initialize_config")
    module.initialize_config(config)
    return config


def _rgbd_files(dataset: Path) -> tuple[list[Path], list[Path]]:
    color_dir = dataset / "color"
    if not color_dir.is_dir():
        for alternative in (dataset / "image", dataset / "rgb"):
            if alternative.is_dir():
                color_dir = alternative
                break
    color = sorted(color_dir.glob("*.jpg")) + sorted(color_dir.glob("*.png"))
    depth = sorted((dataset / "depth").glob("*.png"))
    return color, depth


def validate_dataset(config: dict[str, Any]) -> int:
    import numpy as np
    import open3d as o3d

    dataset = Path(config["path_dataset"])
    intrinsic_path = Path(config["path_intrinsic"])
    if not dataset.is_dir():
        raise ValueError(f"数据集目录不存在: {dataset}")
    if not intrinsic_path.is_file():
        raise ValueError(f"相机内参文件不存在: {intrinsic_path}")
    color, depth = _rgbd_files(dataset)
    if len(color) != len(depth):
        raise ValueError(f"彩色帧和深度帧数量不一致: {len(color)} != {len(depth)}")
    if len(color) < 3:
        raise ValueError(f"至少需要 3 帧 RGB-D 图像，当前只有 {len(color)} 帧")

    intrinsic = o3d.io.read_pinhole_camera_intrinsic(str(intrinsic_path))
    if intrinsic.width <= 0 or intrinsic.height <= 0:
        raise ValueError(f"相机内参无效: {intrinsic_path}")
    color_sample = np.asarray(o3d.io.read_image(str(color[0])))
    depth_sample = np.asarray(o3d.io.read_image(str(depth[0])))
    if color_sample.size == 0 or depth_sample.size == 0:
        raise ValueError("首帧 RGB-D 图像无法读取")
    if color_sample.shape[:2] != depth_sample.shape[:2]:
        raise ValueError(
            f"深度未对齐到彩色图像: color={color_sample.shape}, depth={depth_sample.shape}"
        )
    height, width = color_sample.shape[:2]
    if (intrinsic.width, intrinsic.height) != (width, height):
        raise ValueError(
            "内参尺寸与图像尺寸不匹配: "
            f"intrinsic={intrinsic.width}x{intrinsic.height}, image={width}x{height}"
        )
    if depth_sample.dtype != np.uint16:
        raise ValueError(f"深度帧必须是 16 位毫米图，当前类型: {depth_sample.dtype}")
    return len(color)


def _require_stage_inputs(stage: str, dataset: Path, config: dict[str, Any]) -> None:
    requirements: dict[str, list[Path]] = {
        "register": [dataset / config["folder_fragment"]],
        "refine": [dataset / config["template_global_posegraph_optimized"]],
        "integrate": [dataset / config["template_refined_posegraph_optimized"]],
        "slac": [dataset / config["template_refined_posegraph_optimized"]],
        "slac-integrate": [dataset / config["subfolder_slac"]],
    }
    missing = [path for path in requirements.get(stage, []) if not path.exists()]
    if missing:
        raise RuntimeError(
            f"阶段 {stage} 缺少前置结果: {', '.join(str(path) for path in missing)}"
        )


def run_pipeline(config: dict[str, Any], stages: Iterable[str]) -> Path | None:
    import open3d as o3d

    stages = tuple(stages)
    initialize_upstream_config(config)
    frame_count = validate_dataset(config)
    dataset = Path(config["path_dataset"])
    write_json(dataset / "effective-config.json", config)
    print(f"数据集检查通过：{frame_count} 帧")
    print(f"执行阶段：{', '.join(STAGE_LABELS[item] for item in stages)}")

    timings: dict[str, float] = {}
    for index, stage in enumerate(stages, start=1):
        _require_stage_inputs(stage, dataset, config)
        label = STAGE_LABELS[stage]
        print(f"\n[{index}/{len(stages)}] {label}")
        started = time.monotonic()
        module = importlib.import_module(STAGE_MODULES[stage])
        module.run(config)
        timings[stage] = time.monotonic() - started
        print(f"{label}完成，用时 {timings[stage]:.1f} 秒。")

    mesh_path = dataset / config["template_global_mesh"]
    result: dict[str, Any] = {
        "frame_count": frame_count,
        "stages": list(stages),
        "timings_seconds": timings,
        "total_seconds": sum(timings.values()),
    }
    if "integrate" in stages:
        if not mesh_path.is_file() or mesh_path.stat().st_size == 0:
            raise RuntimeError(f"TSDF 融合结束但没有生成网格: {mesh_path}")
        mesh = o3d.io.read_triangle_mesh(str(mesh_path))
        result["mesh"] = {
            "path": str(mesh_path),
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.triangles),
            "bytes": mesh_path.stat().st_size,
        }
        if len(mesh.vertices) == 0:
            raise RuntimeError("生成的网格没有顶点；请检查深度范围、图像和相机轨迹")
    write_json(dataset / "run-report.json", result)

    print("\n重建阶段全部完成。")
    for stage, elapsed in timings.items():
        print(f"  {STAGE_LABELS[stage]}: {elapsed:.1f} 秒")
    if mesh_path.is_file():
        print(f"最终网格: {mesh_path}")
        return mesh_path
    return None


def run_color_map(
    config: dict[str, Any], *, sample_rate: int = 10, visualize: bool = False
) -> Path:
    if sample_rate <= 0:
        raise ValueError("--sample-rate 必须大于 0")
    import open3d as o3d

    initialize_upstream_config(config)
    validate_dataset(config)
    dataset = Path(config["path_dataset"])
    mesh = dataset / config["template_global_mesh"]
    trajectory = dataset / config["template_global_traj"]
    if not mesh.is_file() or not trajectory.is_file():
        raise RuntimeError("颜色优化需要先完成 integrate 阶段")
    module = importlib.import_module("color_map_optimization_for_reconstruction_system")
    camera = o3d.io.read_pinhole_camera_trajectory(str(trajectory))
    keys = list(range(0, len(camera.parameters), sample_rate))
    if not keys:
        raise RuntimeError("轨迹中没有可用于颜色优化的关键帧")

    original_draw = o3d.visualization.draw_geometries
    if not visualize:
        o3d.visualization.draw_geometries = lambda *_args, **_kwargs: None
    try:
        module.main(config, keys)
    finally:
        o3d.visualization.draw_geometries = original_draw
    output = dataset / config["folder_scene"] / "color_map_after_optimization.ply"
    if not output.is_file():
        raise RuntimeError(f"颜色优化没有生成预期文件: {output}")
    print(f"颜色优化网格: {output}")
    return output

