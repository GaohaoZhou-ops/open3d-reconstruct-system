from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration import read_json_object, write_json
from .paths import ROOT


EXTRACTION_MANIFEST = ".open3d-reconstruct.json"


@dataclass(frozen=True)
class ExtractionWorkspace:
    source: Path
    destination: Path
    partial: Path | None
    stride: int

    @property
    def reused(self) -> bool:
        return self.partial is None


def source_fingerprint(source: Path) -> dict[str, Any]:
    stat = source.stat()
    return {
        "path": str(source.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _color_frames(destination: Path) -> list[Path]:
    color = destination / "color"
    return sorted(
        path
        for pattern in ("*.jpg", "*.jpeg", "*.png")
        for path in color.glob(pattern)
    )


def existing_extraction_matches(
    source: Path, destination: Path, *, stride: int
) -> bool:
    try:
        manifest = read_json_object(destination / EXTRACTION_MANIFEST)
        frames = int(manifest["frame_count"])
        recorded_stride = int(manifest["stride"])
    except (ValueError, KeyError, TypeError):
        return False
    if (
        manifest.get("managed_by") != "open3d-reconstruct"
        or manifest.get("state") != "complete"
        or manifest.get("source") != source_fingerprint(source)
        or recorded_stride != stride
        or frames <= 0
    ):
        return False
    if (
        source.suffix.lower() == ".mkv"
        and manifest.get("camera") == "azure-kinect"
        and manifest.get("alignment")
        not in {"depth-to-color", "color-to-undistorted-depth"}
    ):
        # Older native extractions could be marked complete even when K4A's
        # headless transform engine returned raw, differently-sized images.
        return False
    return (
        (destination / "intrinsic.json").is_file()
        and len(_color_frames(destination)) == frames
        and len(list((destination / "depth").glob("*.png"))) == frames
    )


def _safe_generated_tree(path: Path) -> bool:
    resolved = path.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), ROOT.resolve()}
    if resolved in forbidden or path.is_symlink() or not path.is_dir():
        return False
    try:
        manifest = read_json_object(path / EXTRACTION_MANIFEST)
    except ValueError:
        return False
    return manifest.get("managed_by") == "open3d-reconstruct" and manifest.get(
        "state"
    ) in {"extracting", "complete"}


def prepare_extraction(
    source: Path,
    destination: Path,
    *,
    suffix: str,
    format_name: str,
    force: bool,
    stride: int,
) -> ExtractionWorkspace:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != suffix:
        raise ValueError(f"{format_name} 输入不存在或扩展名不正确: {source}")
    if stride <= 0:
        raise ValueError("--stride 必须大于 0")
    try:
        source.relative_to(destination)
    except ValueError:
        pass
    else:
        raise ValueError(f"数据集输出目录不能包含输入 {format_name}，否则覆盖时会删除输入")

    if destination.exists():
        if not force and existing_extraction_matches(
            source, destination, stride=stride
        ):
            print(f"帧数据已完整提取，直接复用：{destination}")
            return ExtractionWorkspace(source, destination, None, stride)
        if not force:
            raise FileExistsError(
                f"数据集目录已存在且与输入或 stride 不匹配: {destination}"
                "（使用 --force 重建）"
            )
        if not _safe_generated_tree(destination):
            raise ValueError(
                f"拒绝删除非本程序生成的目录: {destination}；请换一个空目录"
            )
        shutil.rmtree(destination)

    partial = destination.with_name(destination.name + ".extracting")
    try:
        source.relative_to(partial.resolve())
    except ValueError:
        pass
    else:
        raise ValueError(
            f"临时提取目录不能包含输入 {format_name}，否则恢复时会删除输入"
        )
    if partial.exists():
        if not _safe_generated_tree(partial):
            raise ValueError(f"发现未知的临时目录，拒绝覆盖: {partial}")
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    write_json(
        partial / EXTRACTION_MANIFEST,
        {
            "managed_by": "open3d-reconstruct",
            "format_version": 1,
            "state": "extracting",
            "source": source_fingerprint(source),
            "source_format": format_name,
            "frame_count": 0,
            "stride": stride,
        },
    )
    return ExtractionWorkspace(source, destination, partial, stride)


def complete_extraction(
    workspace: ExtractionWorkspace,
    *,
    frame_count: int,
    source_frame_count: int,
    depth_scale: float,
    manifest_extra: dict[str, Any] | None = None,
) -> Path:
    if workspace.partial is None:
        return workspace.destination
    if frame_count <= 0:
        raise RuntimeError("录制中没有可读取的同步 RGB-D 帧")
    partial = workspace.partial
    color_count = len(_color_frames(partial))
    depth_count = len(list((partial / "depth").glob("*.png")))
    if color_count != frame_count or depth_count != frame_count:
        raise RuntimeError(
            "提取结果不完整: "
            f"记录 {frame_count} 帧，实际 color={color_count}, depth={depth_count}"
        )
    if not (partial / "intrinsic.json").is_file():
        raise RuntimeError("提取结束但未生成相机内参")

    manifest: dict[str, Any] = {
        "managed_by": "open3d-reconstruct",
        "format_version": 1,
        "state": "complete",
        "source": source_fingerprint(workspace.source),
        "frame_count": frame_count,
        "source_frame_count": source_frame_count,
        "stride": workspace.stride,
        "depth_scale": float(depth_scale),
    }
    if manifest_extra:
        protected = {"managed_by", "state", "source", "frame_count", "stride"}
        overlap = protected.intersection(manifest_extra)
        if overlap:
            raise ValueError(f"提取清单扩展字段不能覆盖保留字段: {sorted(overlap)}")
        manifest.update(manifest_extra)
    write_json(partial / EXTRACTION_MANIFEST, manifest)
    write_json(
        partial / "config.json",
        {
            "path_dataset": str(workspace.destination),
            "path_intrinsic": str(workspace.destination / "intrinsic.json"),
            "depth_scale": float(depth_scale),
        },
    )
    partial.replace(workspace.destination)
    return workspace.destination
