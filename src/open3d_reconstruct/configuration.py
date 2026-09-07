from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .compute import normalize_compute_backend
from .paths import DEFAULT_RECONSTRUCTION_CONFIG


ALLOWED_ICP_METHODS = {"point_to_point", "point_to_plane", "color", "generalized"}
ALLOWED_GLOBAL_REGISTRATION = {"ransac", "fgr"}
DEFAULT_PIPELINE_STAGES = ("make", "register", "refine", "integrate")
ALLOWED_PIPELINE_STAGES = (
    "make",
    "register",
    "refine",
    "integrate",
    "slac",
    "slac-integrate",
)

# These are exactly the controls exposed by the Web reconstruction dialog.  The
# same rules are used for YAML profiles and HTTP requests so that neither entry
# point can silently accept a setting rejected by the other.
RECONSTRUCTION_PARAMETER_RULES: dict[str, dict[str, Any]] = {
    "n_frames_per_fragment": {
        "kind": "integer",
        "minimum": 30,
        "maximum": 300,
    },
    "n_keyframes_per_n_frame": {
        "kind": "integer",
        "minimum": 2,
        "maximum": 30,
    },
    "depth_min": {"kind": "number", "minimum": 0.0, "maximum": 5.0},
    "depth_max": {"kind": "number", "minimum": 0.5, "maximum": 10.0},
    "voxel_size": {"kind": "number", "minimum": 0.01, "maximum": 0.2},
    "depth_diff_max": {"kind": "number", "minimum": 0.01, "maximum": 0.3},
    "tsdf_cubic_size": {"kind": "number", "minimum": 0.512, "maximum": 10.24},
    "sdf_trunc": {"kind": "number", "minimum": 0.005, "maximum": 0.2},
    "preference_loop_closure_odometry": {
        "kind": "number",
        "minimum": 0.01,
        "maximum": 20.0,
    },
    "preference_loop_closure_registration": {
        "kind": "number",
        "minimum": 0.01,
        "maximum": 20.0,
    },
    "icp_method": {
        "kind": "choice",
        "choices": ALLOWED_ICP_METHODS,
    },
    "global_registration": {
        "kind": "choice",
        "choices": ALLOWED_GLOBAL_REGISTRATION,
    },
}


@dataclass(frozen=True)
class ReconstructionPlan:
    """Resolved execution settings from either a profile catalog or flat config."""

    source: Path
    profile: str | None
    label: str
    description: str
    stride: int
    stages: tuple[str, ...]
    compute_backend: str
    compute_device: str
    parameters: dict[str, Any]
    catalog: bool


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


def read_config_object(path: Path) -> dict[str, Any]:
    """Read a JSON or YAML mapping without executing YAML constructors."""

    suffix = path.suffix.lower()
    if suffix == ".json":
        return read_json_object(path)
    if suffix not in {".yaml", ".yml"}:
        raise ValueError(f"配置文件只支持 .json、.yaml 或 .yml: {path}")
    try:
        import yaml

        with path.open("r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise ValueError(f"配置文件不存在: {path}") from exc
    except Exception as exc:
        # PyYAML exposes its parser exception types from the yaml module, but a
        # broad conversion here keeps malformed files and decoder failures under
        # the CLI's normal, concise configuration error handling.
        raise ValueError(f"配置文件不是有效 YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是对象: {path}")
    return value


def normalize_reconstruction_parameters(value: object) -> dict[str, Any]:
    """Validate and normalize the parameters shared by Web and YAML."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("重建参数必须是对象")
    unknown = sorted(set(value) - set(RECONSTRUCTION_PARAMETER_RULES))
    if unknown:
        raise ValueError(f"不支持的重建参数: {', '.join(unknown)}")

    result: dict[str, Any] = {}
    for key, rule in RECONSTRUCTION_PARAMETER_RULES.items():
        if key not in value:
            continue
        raw = value[key]
        kind = rule["kind"]
        if kind == "integer":
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise ValueError(f"重建参数 {key} 必须是整数")
            parsed: Any = raw
        elif kind == "number":
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise ValueError(f"重建参数 {key} 必须是数值")
            parsed = float(raw)
            if not math.isfinite(parsed):
                raise ValueError(f"重建参数 {key} 必须是有限数值")
        else:
            if not isinstance(raw, str) or raw not in rule["choices"]:
                choices = ", ".join(sorted(rule["choices"]))
                raise ValueError(f"重建参数 {key} 必须是以下值之一: {choices}")
            result[key] = raw
            continue

        if parsed < rule["minimum"] or parsed > rule["maximum"]:
            raise ValueError(
                f"重建参数 {key} 必须在 {rule['minimum']} 到 {rule['maximum']} 之间"
            )
        result[key] = parsed

    frames = result.get("n_frames_per_fragment")
    interval = result.get("n_keyframes_per_n_frame")
    if frames is not None and interval is not None and interval > frames:
        raise ValueError("关键帧间隔不能大于每个局部片段的帧数")
    depth_min = result.get("depth_min")
    depth_max = result.get("depth_max")
    if depth_min is not None and depth_max is not None and depth_min >= depth_max:
        raise ValueError("最小深度必须小于最大深度")
    cubic_size = result.get("tsdf_cubic_size")
    sdf_trunc = result.get("sdf_trunc")
    if (
        cubic_size is not None
        and sdf_trunc is not None
        and sdf_trunc < cubic_size / 512.0
    ):
        raise ValueError("SDF 截断距离不能小于 TSDF 融合体素边长")
    return result


def _normalize_stride(value: object, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1000:
        raise ValueError(f"{context} stride 必须是 1 到 1000 之间的整数")
    return value


def _normalize_stages(value: object) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_PIPELINE_STAGES
    if isinstance(value, str):
        stages = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        stages = tuple(item.strip().lower() for item in value if item.strip())
    else:
        raise ValueError("stages 必须是阶段名称列表或逗号分隔字符串")
    if not stages:
        raise ValueError("stages 不能为空")
    unknown = [item for item in stages if item not in ALLOWED_PIPELINE_STAGES]
    if unknown:
        raise ValueError(f"未知重建阶段: {', '.join(unknown)}")
    if len(set(stages)) != len(stages):
        raise ValueError("stages 中不能重复指定阶段")
    order = [ALLOWED_PIPELINE_STAGES.index(item) for item in stages]
    if order != sorted(order):
        raise ValueError(
            "重建阶段必须按以下顺序排列: " + ", ".join(ALLOWED_PIPELINE_STAGES)
        )
    return stages


def _text_field(value: object, *, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串")
    return value.strip()


def load_reconstruction_plan(
    path: Path,
    profile: str | None = None,
) -> ReconstructionPlan:
    """Resolve a profile from the portable YAML catalog or a legacy flat file."""

    resolved = path.expanduser().resolve()
    document = read_config_object(resolved)
    profiles = document.get("profiles")
    if profiles is None:
        if profile is not None:
            raise ValueError("扁平重建配置不包含 profiles，不能指定 --profile")
        backend = normalize_compute_backend(document.get("compute_backend", "auto"))
        device = _text_field(
            document.get("device", document.get("compute_device", "CPU:0")),
            name="compute_device",
        )
        return ReconstructionPlan(
            source=resolved,
            profile=None,
            label=resolved.stem,
            description="兼容的扁平重建配置",
            stride=1,
            stages=DEFAULT_PIPELINE_STAGES,
            compute_backend=backend,
            compute_device=device or "CPU:0",
            parameters=dict(document),
            catalog=False,
        )

    allowed_top_level = {
        "version",
        "default_profile",
        "compute_backend",
        "compute_device",
        "stages",
        "parameters",
        "profiles",
    }
    unknown_top = sorted(set(document) - allowed_top_level)
    if unknown_top:
        raise ValueError(f"YAML 顶层包含未知字段: {', '.join(unknown_top)}")
    if document.get("version", 1) != 1:
        raise ValueError("仅支持 version: 1 的重建 YAML")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles 必须是非空对象")
    if any(not isinstance(name, str) or not name.strip() for name in profiles):
        raise ValueError("profiles 的档位名称必须是非空字符串")

    selected_name = _text_field(
        profile if profile is not None else document.get("default_profile"),
        name="profile",
    )
    if not selected_name:
        raise ValueError("YAML 必须设置 default_profile，或通过 --profile 指定")
    selected = profiles.get(selected_name)
    if not isinstance(selected, dict):
        choices = ", ".join(str(item) for item in profiles)
        raise ValueError(f"配置档位不存在: {selected_name}；可用值: {choices}")
    allowed_profile = {"label", "description", "stride", "parameters"}
    unknown_profile = sorted(set(selected) - allowed_profile)
    if unknown_profile:
        raise ValueError(
            f"profiles.{selected_name} 包含未知字段: {', '.join(unknown_profile)}"
        )

    defaults = read_json_object(DEFAULT_RECONSTRUCTION_CONFIG)
    default_parameters = normalize_reconstruction_parameters(
        {
            key: defaults[key]
            for key in RECONSTRUCTION_PARAMETER_RULES
            if key in defaults
        }
    )
    common = normalize_reconstruction_parameters(document.get("parameters"))
    chosen = normalize_reconstruction_parameters(selected.get("parameters"))
    parameters = {**default_parameters, **common, **chosen}
    # Validate cross-field relationships after common/profile values are merged.
    parameters = normalize_reconstruction_parameters(parameters)
    stride = _normalize_stride(
        selected.get("stride", 1), context=f"profiles.{selected_name}"
    )
    backend = normalize_compute_backend(document.get("compute_backend", "auto"))
    device = _text_field(
        document.get("compute_device", "CPU:0"), name="compute_device"
    )
    return ReconstructionPlan(
        source=resolved,
        profile=selected_name,
        label=_text_field(selected.get("label"), name="label", default=selected_name)
        or selected_name,
        description=_text_field(selected.get("description"), name="description"),
        stride=stride,
        stages=_normalize_stages(document.get("stages")),
        compute_backend=backend,
        compute_device=device or "CPU:0",
        parameters=parameters,
        catalog=True,
    )


def reconstruction_profile_catalog(path: Path) -> dict[str, Any]:
    """Return validated profiles in a JSON-safe shape for Web and the wizard."""

    resolved = path.expanduser().resolve()
    document = read_config_object(resolved)
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"配置文件不是多档位 YAML: {resolved}")
    default_profile = _text_field(
        document.get("default_profile"), name="default_profile"
    )
    values: dict[str, Any] = {}
    for name in profiles:
        plan = load_reconstruction_plan(resolved, str(name))
        values[str(name)] = {
            "label": plan.label,
            "description": plan.description,
            "stride": plan.stride,
            "parameters": plan.parameters,
        }
    if default_profile not in values:
        raise ValueError(f"default_profile 指向不存在的档位: {default_profile}")
    return {
        "source": str(resolved),
        "default_profile": default_profile,
        "compute_backend": normalize_compute_backend(
            document.get("compute_backend", "auto")
        ),
        "compute_device": _text_field(
            document.get("compute_device", "CPU:0"), name="compute_device"
        )
        or "CPU:0",
        "stages": list(_normalize_stages(document.get("stages"))),
        "profiles": values,
    }


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
    device: str | None = None,
    compute_backend: str | None = None,
    profile: str | None = None,
    plan: ReconstructionPlan | None = None,
) -> dict[str, Any]:
    config = read_json_object(DEFAULT_RECONSTRUCTION_CONFIG)
    dataset_config_path = dataset / "config.json"
    if dataset_config_path.is_file():
        dataset_config = read_json_object(dataset_config_path)
        if "depth_scale" in dataset_config:
            config["depth_scale"] = dataset_config["depth_scale"]
    selected_plan = plan
    if selected_plan is None and config_path is not None:
        selected_plan = load_reconstruction_plan(config_path, profile)
    if selected_plan is not None and not (
        not selected_plan.catalog
        and selected_plan.source.resolve() == DEFAULT_RECONSTRUCTION_CONFIG.resolve()
    ):
        config.update(selected_plan.parameters)
    config.update(parse_overrides(overrides))
    config["path_dataset"] = str(dataset.resolve())
    config["path_intrinsic"] = str(intrinsic.resolve())
    config["debug_mode"] = bool(debug)
    configured_device = device or (
        selected_plan.compute_device if selected_plan is not None else "CPU:0"
    )
    configured_backend = compute_backend or (
        selected_plan.compute_backend if selected_plan is not None else "auto"
    )
    config["device"] = configured_device
    config["compute_backend"] = normalize_compute_backend(configured_backend)
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
        "sdf_trunc",
        "preference_loop_closure_odometry",
        "preference_loop_closure_registration",
    ):
        _positive_number(config, key)
    depth_min = config.get("depth_min", 0)
    if not isinstance(depth_min, (int, float)) or isinstance(depth_min, bool):
        raise ValueError(f"重建参数 depth_min 必须是数值，当前值: {depth_min!r}")
    if depth_min < 0:
        raise ValueError("重建参数 depth_min 不能小于 0")
    if depth_min >= config["depth_max"]:
        raise ValueError("depth_min 必须小于 depth_max")
    if config["sdf_trunc"] < config["tsdf_cubic_size"] / 512.0:
        raise ValueError("sdf_trunc 不能小于 TSDF 融合体素边长")
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
    normalize_compute_backend(config.get("compute_backend", "auto"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)
