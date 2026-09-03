from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .paths import DEFAULT_RECONSTRUCTION_CONFIG


ALLOWED_ICP_METHODS = {"point_to_point", "point_to_plane", "color", "generalized"}
ALLOWED_GLOBAL_REGISTRATION = {"ransac", "fgr"}


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError as exc:
        raise ValueError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是 JSON 对象: {path}")
    return value


def parse_overrides(values: Iterable[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"参数覆盖应写成 key=value: {item}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数覆盖缺少键名: {item}")
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        overrides[key] = value
    return overrides


def build_reconstruction_config(
    dataset: Path,
    intrinsic: Path,
    config_path: Path | None = None,
    overrides: Iterable[str] = (),
    *,
    debug: bool = False,
    single_thread: bool = False,
    device: str = "CPU:0",
) -> dict[str, Any]:
    config = read_json_object(DEFAULT_RECONSTRUCTION_CONFIG)
    dataset_config_path = dataset / "config.json"
    if dataset_config_path.is_file():
        dataset_config = read_json_object(dataset_config_path)
        if "depth_scale" in dataset_config:
            config["depth_scale"] = dataset_config["depth_scale"]
    if config_path is not None and config_path.resolve() != DEFAULT_RECONSTRUCTION_CONFIG.resolve():
        config.update(read_json_object(config_path))
    config.update(parse_overrides(overrides))
    config["path_dataset"] = str(dataset.resolve())
    config["path_intrinsic"] = str(intrinsic.resolve())
    config["debug_mode"] = bool(debug)
    config["device"] = device
    if single_thread:
        config["python_multi_threading"] = False
    validate_reconstruction_config(config)
    return config


def _positive_number(config: dict[str, Any], key: str) -> None:
    value = config.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"重建参数 {key} 必须是正数，当前值: {value!r}")


def _positive_integer(config: dict[str, Any], key: str) -> None:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"重建参数 {key} 必须是正整数，当前值: {value!r}")


def validate_reconstruction_config(config: dict[str, Any]) -> None:
    for key in ("n_frames_per_fragment", "n_keyframes_per_n_frame"):
        _positive_integer(config, key)
    for key in (
        "depth_max",
        "depth_scale",
        "voxel_size",
        "depth_diff_max",
        "tsdf_cubic_size",
    ):
        _positive_number(config, key)
    depth_min = config.get("depth_min", 0)
    if not isinstance(depth_min, (int, float)) or isinstance(depth_min, bool):
        raise ValueError(f"重建参数 depth_min 必须是数值，当前值: {depth_min!r}")
    if depth_min < 0:
        raise ValueError("重建参数 depth_min 不能小于 0")
    if depth_min >= config["depth_max"]:
        raise ValueError("depth_min 必须小于 depth_max")
    if config.get("icp_method") not in ALLOWED_ICP_METHODS:
        raise ValueError(
            f"icp_method 必须是 {sorted(ALLOWED_ICP_METHODS)} 之一"
        )
    if config.get("global_registration") not in ALLOWED_GLOBAL_REGISTRATION:
        raise ValueError(
            "global_registration 必须是 "
            f"{sorted(ALLOWED_GLOBAL_REGISTRATION)} 之一"
        )
    if not isinstance(config.get("python_multi_threading"), bool):
        raise ValueError("python_multi_threading 必须是 true 或 false")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)
