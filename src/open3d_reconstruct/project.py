from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .configuration import read_json_object, write_json


PROJECT_FILENAME = "open3d-project.json"
PROJECT_KIND = "open3d-reconstruct-project"
PROJECT_FORMAT_VERSION = 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _relative_file(dataset: Path, candidate: Path) -> str | None:
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(dataset.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        return None
    return relative.as_posix()


def _known_artifacts(dataset: Path) -> dict[str, str]:
    candidates = {
        "intrinsic": dataset / "intrinsic.json",
        "extraction_manifest": dataset / ".open3d-reconstruct.json",
        "effective_config": dataset / "effective-config.json",
        "run_report": dataset / "run-report.json",
        "mesh": dataset / "scene" / "integrated.ply",
        "trajectory": dataset / "scene" / "trajectory.log",
        "global_registration": dataset / "scene" / "global_registration.json",
        "global_registration_optimized": (
            dataset / "scene" / "global_registration_optimized.json"
        ),
        "refined_registration": dataset / "scene" / "refined_registration.json",
        "refined_registration_optimized": (
            dataset / "scene" / "refined_registration_optimized.json"
        ),
    }
    return {
        key: relative
        for key, path in candidates.items()
        if (relative := _relative_file(dataset, path)) is not None
    }


def _known_directories(dataset: Path) -> dict[str, str]:
    candidates = {
        "color": dataset / "color",
        "depth": dataset / "depth",
        "fragments": dataset / "fragments",
        "scene": dataset / "scene",
        "slac": dataset / "slac",
    }
    return {
        key: path.relative_to(dataset).as_posix()
        for key, path in candidates.items()
        if path.is_dir()
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        return read_json_object(path) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _serializable_matching(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scalar_keys = (
        "frame_count",
        "frames_per_fragment",
        "expected",
        "attempted",
        "succeeded",
        "failed",
        "information_max",
        "last",
    )
    result = {key: value.get(key) for key in scalar_keys if key in value}
    result["active"] = list(value.get("active") or [])
    result["events"] = list(value.get("events") or [])
    return result


def _serializable_analysis(conversion: object) -> dict[str, Any]:
    if not isinstance(conversion, dict):
        return {}
    keys = (
        "stage",
        "stage_index",
        "stage_count",
        "label",
        "status",
        "detail",
        "processed",
        "total",
        "frame_count",
        "fragment_completed",
        "fragment_total",
        "elapsed_seconds",
        "stage_elapsed_seconds",
        "pause_count",
        "paused_total_seconds",
        "settings",
    )
    result = {key: conversion.get(key) for key in keys if key in conversion}
    matching = _serializable_matching(conversion.get("matching"))
    if matching:
        result["matching"] = matching
    return result


def _clean_logs(logs: Iterable[object] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in logs or ():
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        result.append(
            {
                "time": str(item.get("time") or ""),
                "level": str(item.get("level") or "output"),
                "message": message,
            }
        )
    return result


def write_reconstruction_project(
    dataset: Path,
    *,
    recording: Path | None = None,
    hardware: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    settings: dict[str, Any] | None = None,
    conversion: dict[str, Any] | None = None,
    logs: Iterable[object] | None = None,
) -> dict[str, Any]:
    dataset = dataset.expanduser().resolve(strict=True)
    if not dataset.is_dir():
        raise ValueError(f"重建工程目录不存在: {dataset}")
    artifacts = _known_artifacts(dataset)
    if "mesh" not in artifacts:
        raise ValueError("重建工程缺少 scene/integrated.ply")

    project_path = dataset / PROJECT_FILENAME
    previous = _read_optional_json(project_path)
    extraction = _read_optional_json(dataset / ".open3d-reconstruct.json")
    report = _read_optional_json(dataset / "run-report.json")
    effective_config = _read_optional_json(dataset / "effective-config.json")
    now = _now_iso()

    recording_value: dict[str, Any] | None = None
    if recording is not None:
        absolute_recording = recording.expanduser().resolve(strict=False)
        recording_value = {
            "path": str(absolute_recording),
            "name": absolute_recording.name,
            "exists": absolute_recording.is_file(),
        }
        try:
            relative_recording = os.path.relpath(absolute_recording, dataset)
        except ValueError:
            relative_recording = None
        if relative_recording is not None:
            recording_value["relative_path"] = Path(relative_recording).as_posix()
    elif isinstance(previous.get("recording"), dict):
        recording_value = dict(previous["recording"])

    frame_count = report.get("frame_count", extraction.get("frame_count"))
    analysis = _serializable_analysis(conversion)
    compute = dict(report.get("compute") or {})
    if not compute and effective_config:
        compute = {
            "requested": effective_config.get("compute_backend"),
            "backend": effective_config.get("compute_backend_resolved"),
            "device": effective_config.get("compute_backend_device"),
            "accelerated": effective_config.get("compute_accelerated"),
            "detail": effective_config.get("compute_backend_detail"),
        }
    mesh_summary = dict(report.get("mesh") or {})
    if mesh_summary:
        mesh_summary["path"] = artifacts["mesh"]
    project: dict[str, Any] = {
        "kind": PROJECT_KIND,
        "format_version": PROJECT_FORMAT_VERSION,
        "application": {
            "name": "open3d-reconstruct-system",
            "version": __version__,
        },
        "created_at": str(previous.get("created_at") or finished_at or now),
        "updated_at": now,
        "status": "completed",
        "dataset": ".",
        "hardware": hardware or extraction.get("camera"),
        "recording": recording_value,
        "artifacts": artifacts,
        "directories": _known_directories(dataset),
        "reconstruction": {
            "started_at": started_at,
            "finished_at": finished_at or now,
            "settings": dict(settings or {}),
            "frame_count": frame_count,
            "stages": list(
                report.get("stages")
                or (["make", "register", "refine", "integrate"] if artifacts.get("mesh") else [])
            ),
            "timings_seconds": dict(report.get("timings_seconds") or {}),
            "total_seconds": report.get(
                "total_seconds", analysis.get("elapsed_seconds")
            ),
            "compute": compute,
            "mesh": mesh_summary,
        },
        "web_analysis": analysis,
        "logs": _clean_logs(logs),
    }
    if not project["reconstruction"]["settings"]:
        project["reconstruction"]["settings"] = {
            key: value
            for key, value in effective_config.items()
            if key not in {"path_dataset", "path_intrinsic"}
        }
    write_json(project_path, project)
    return project


def _resolve_artifact(dataset: Path, value: object, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw or Path(raw).is_absolute():
        raise ValueError(f"工程文件中的 {label} 路径无效")
    try:
        resolved = (dataset / raw).resolve(strict=True)
        resolved.relative_to(dataset)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"工程文件中的 {label} 不存在或超出工程目录") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ValueError(f"工程文件中的 {label} 为空或不可读")
    return resolved


def _resolve_recording(dataset: Path, value: object) -> Path | None:
    if not isinstance(value, dict):
        return None
    candidates: list[Path] = []
    relative = str(value.get("relative_path") or "").strip()
    absolute = str(value.get("path") or "").strip()
    if relative and not Path(relative).is_absolute():
        candidates.append(dataset / relative)
    if absolute:
        candidates.append(Path(absolute).expanduser())
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.suffix.lower() in {".mkv", ".bag"} and resolved.is_file():
            return resolved
    return None


def load_reconstruction_project(directory: Path) -> dict[str, Any]:
    dataset = directory.expanduser().resolve(strict=True)
    if not dataset.is_dir():
        raise ValueError("所选工程路径不是目录")
    project_path = dataset / PROJECT_FILENAME
    if project_path.is_file():
        project = read_json_object(project_path)
        if project.get("kind") != PROJECT_KIND:
            raise ValueError("工程配置文件类型不受支持")
        if project.get("format_version") != PROJECT_FORMAT_VERSION:
            raise ValueError(
                f"工程配置版本不受支持: {project.get('format_version')}"
            )
    else:
        project = write_reconstruction_project(dataset)

    artifacts = project.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("工程配置缺少产物索引")
    mesh = _resolve_artifact(dataset, artifacts.get("mesh"), "最终模型")
    reconstruction = project.get("reconstruction")
    if not isinstance(reconstruction, dict):
        raise ValueError("工程配置中的 reconstruction 必须是对象")
    for key in ("settings", "timings_seconds", "compute", "mesh"):
        if key in reconstruction and not isinstance(reconstruction[key], dict):
            raise ValueError(f"工程配置中的 reconstruction.{key} 必须是对象")
    if "stages" in reconstruction and not isinstance(
        reconstruction["stages"], list
    ):
        raise ValueError("工程配置中的 reconstruction.stages 必须是数组")
    analysis = project.get("web_analysis")
    if not isinstance(analysis, dict):
        raise ValueError("工程配置中的 web_analysis 必须是对象")
    matching = analysis.get("matching")
    if matching is not None and not isinstance(matching, dict):
        raise ValueError("工程配置中的 web_analysis.matching 必须是对象")
    if isinstance(matching, dict):
        for key in ("attempted", "succeeded", "failed", "information_max"):
            if key in matching and matching[key] is not None and not isinstance(
                matching[key], (int, float)
            ):
                raise ValueError(
                    f"工程配置中的 web_analysis.matching.{key} 必须是数字"
                )
        for key in ("events", "active"):
            if key in matching and not isinstance(matching[key], list):
                raise ValueError(
                    f"工程配置中的 web_analysis.matching.{key} 必须是数组"
                )
    if "logs" in project and not isinstance(project["logs"], list):
        raise ValueError("工程配置中的 logs 必须是数组")
    return {
        "path": project_path,
        "dataset": dataset,
        "mesh": mesh,
        "recording": _resolve_recording(dataset, project.get("recording")),
        "hardware": project.get("hardware"),
        "reconstruction": reconstruction,
        "analysis": analysis,
        "logs": _clean_logs(project.get("logs")),
        "manifest": project,
    }
