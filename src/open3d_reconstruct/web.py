from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import psutil

from . import __version__
from .compute import resolve_compute_backend
from .configuration import (
    normalize_reconstruction_parameters,
    read_json_object,
    reconstruction_profile_catalog,
)
from .paths import (
    DATASETS_DIR,
    DEFAULT_AZURE_SENSOR_CONFIG,
    DEFAULT_REALSENSE_SENSOR_CONFIG,
    DEFAULT_RECONSTRUCTION_PROFILES,
    IS_WSL,
    RECORDINGS_DIR,
    ROOT,
    RUNTIME_DIR,
    ensure_local_directories,
)
from .project import (
    PROJECT_FILENAME,
    load_reconstruction_project,
    write_reconstruction_project,
)
from .service import (
    DEFAULT_SERVICE_PORT,
    SERVICE_NAME,
    SingletonLease,
    _format_command,
    _launcher_prefix,
)


DEFAULT_WEB_PORT = DEFAULT_SERVICE_PORT
WEB_HOST = "127.0.0.1"
WEBUI_DIR = Path(__file__).resolve().parent / "webui"
MAX_REQUEST_BYTES = 32 * 1024
MAX_LOG_LINES = 1200
MAX_MATCHING_EVENTS = 2000
MAX_RECORDING_UPLOAD_BYTES = 256 * 1024**3
RECORDING_UPLOAD_CHUNK_BYTES = 4 * 1024**2
MIN_FREE_STORAGE_BYTES = 256 * 1024**2
MESH_PREVIEW_TARGET_TRIANGLES = 500_000
MESH_PREVIEW_CACHE_VERSION = 2
POINT_CLOUD_SUFFIXES = frozenset({".ply"})
WEB_MATCH_PREFIX = "__OPEN3D_WEB_MATCH__ "
PIPELINE_STEPS = (
    ("extract", "提取 RGB-D 帧"),
    ("make", "生成局部片段"),
    ("register", "全局片段配准"),
    ("refine", "精细配准"),
    ("integrate", "TSDF 场景融合"),
)
PIPELINE_LABEL_TO_KEY = {label: key for key, label in PIPELINE_STEPS}

HARDWARE: dict[str, dict[str, str]] = {
    "azure-kinect": {
        "label": "Azure Kinect DK",
        "camera": "azure-kinect",
        "extension": ".mkv",
    },
    "d435": {
        "label": "Intel RealSense D435",
        "camera": "realsense",
        "extension": ".bag",
    },
    "d435i": {
        "label": "Intel RealSense D435i",
        "camera": "realsense",
        "extension": ".bag",
    },
}

CAMERA_PARAMETER_DEFINITIONS: dict[str, tuple[dict[str, str], ...]] = {
    "azure-kinect": (
        {
            "key": "color_resolution",
            "label": "彩色分辨率",
            "impact": "决定纹理清晰度，也会影响 USB 带宽、解码负载和文件大小。",
        },
        {
            "key": "color_format",
            "label": "彩色编码",
            "impact": "MJPG 可显著降低 USB 带宽，但需要 CPU 解码。",
        },
        {
            "key": "depth_mode",
            "label": "深度模式",
            "impact": "直接决定深度视场、分辨率、量程与噪声水平。",
        },
        {
            "key": "camera_fps",
            "label": "采集帧率",
            "impact": "帧率越高越利于连续运动匹配，同时增加带宽与存储压力。",
        },
        {
            "key": "synchronized_images_only",
            "label": "仅同步 RGB-D",
            "impact": "只保留成对同步的彩色与深度帧，避免时间错位。",
        },
        {
            "key": "depth_delay_off_color_usec",
            "label": "深度相对彩色延迟",
            "impact": "多传感器或精确时序场景使用；单机通常保持 0。",
        },
        {
            "key": "wired_sync_mode",
            "label": "有线同步模式",
            "impact": "控制单机、主机或从机工作方式。",
        },
        {
            "key": "subordinate_delay_off_master_usec",
            "label": "从机相对主机延迟",
            "impact": "仅在有线多机同步时生效。",
        },
        {
            "key": "disable_streaming_indicator",
            "label": "关闭录制指示灯",
            "impact": "只影响设备指示灯，不影响重建质量。",
        },
    ),
    "realsense": (
        {
            "key": "serial",
            "label": "绑定序列号",
            "impact": "留空时按页面选择的设备编号连接；填写后固定到指定相机。",
        },
        {
            "key": "color_resolution",
            "label": "彩色分辨率",
            "impact": "决定纹理清晰度，并影响带宽与文件大小。",
        },
        {
            "key": "color_format",
            "label": "彩色格式",
            "impact": "决定相机输出到 Open3D 的颜色像素格式。",
        },
        {
            "key": "depth_resolution",
            "label": "深度分辨率",
            "impact": "直接影响可用几何细节、噪声与带宽。",
        },
        {
            "key": "depth_format",
            "label": "深度格式",
            "impact": "Z16 保存 16 位原始深度，是当前重建链路所需格式。",
        },
        {
            "key": "fps",
            "label": "采集帧率",
            "impact": "帧率越高越利于连续运动匹配，同时增加带宽与存储压力。",
        },
        {
            "key": "visual_preset",
            "label": "硬件深度预设",
            "impact": "RealSense 芯片侧深度算法预设，直接影响噪声、完整度与精度。",
        },
    ),
}

CAMERA_PARAMETER_DISPLAY_VALUES: dict[tuple[str, str], dict[str, str]] = {
    ("azure-kinect", "color_resolution"): {
        "K4A_COLOR_RESOLUTION_720P": "1280 × 720",
        "K4A_COLOR_RESOLUTION_1080P": "1920 × 1080",
        "K4A_COLOR_RESOLUTION_1440P": "2560 × 1440",
        "K4A_COLOR_RESOLUTION_1536P": "2048 × 1536",
        "K4A_COLOR_RESOLUTION_2160P": "3840 × 2160",
        "K4A_COLOR_RESOLUTION_3072P": "4096 × 3072",
    },
    ("azure-kinect", "color_format"): {
        "K4A_IMAGE_FORMAT_COLOR_MJPG": "MJPG（压缩传输）",
        "K4A_IMAGE_FORMAT_COLOR_NV12": "NV12",
        "K4A_IMAGE_FORMAT_COLOR_YUY2": "YUY2",
        "K4A_IMAGE_FORMAT_COLOR_BGRA32": "BGRA32",
    },
    ("azure-kinect", "depth_mode"): {
        "K4A_DEPTH_MODE_NFOV_2X2BINNED": "NFOV 2×2 Binned · 320 × 288",
        "K4A_DEPTH_MODE_NFOV_UNBINNED": "NFOV Unbinned · 640 × 576",
        "K4A_DEPTH_MODE_WFOV_2X2BINNED": "WFOV 2×2 Binned · 512 × 512",
        "K4A_DEPTH_MODE_WFOV_UNBINNED": "WFOV Unbinned · 1024 × 1024",
        "K4A_DEPTH_MODE_PASSIVE_IR": "Passive IR · 1024 × 1024",
    },
    ("azure-kinect", "camera_fps"): {
        "K4A_FRAMES_PER_SECOND_5": "5 FPS",
        "K4A_FRAMES_PER_SECOND_15": "15 FPS",
        "K4A_FRAMES_PER_SECOND_30": "30 FPS",
    },
    ("azure-kinect", "wired_sync_mode"): {
        "K4A_WIRED_SYNC_MODE_STANDALONE": "单机",
        "K4A_WIRED_SYNC_MODE_MASTER": "主机",
        "K4A_WIRED_SYNC_MODE_SUBORDINATE": "从机",
    },
    ("realsense", "visual_preset"): {
        "RS2_RS400_VISUAL_PRESET_CUSTOM": "Custom",
        "RS2_RS400_VISUAL_PRESET_DEFAULT": "Default",
        "RS2_RS400_VISUAL_PRESET_HAND": "Hand",
        "RS2_RS400_VISUAL_PRESET_HIGH_ACCURACY": "High Accuracy（高精度）",
        "RS2_RS400_VISUAL_PRESET_HIGH_DENSITY": "High Density（高密度）",
        "RS2_RS400_VISUAL_PRESET_MEDIUM_DENSITY": "Medium Density（中密度）",
    },
}

class WebActionError(RuntimeError):
    def __init__(self, message: str, status: int = HTTPStatus.CONFLICT) -> None:
        super().__init__(message)
        self.status = int(status)


def _translate_wsl_path(value: str | Path, *, to_windows: bool) -> str:
    converter = shutil.which("wslpath")
    if converter is None:
        raise WebActionError(
            "WSL 缺少 wslpath，无法转换 Windows 文件路径",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    try:
        result = subprocess.run(
            [converter, "-w" if to_windows else "-u", str(value)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise WebActionError(
            f"无法运行 WSL 路径转换器: {exc}",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        ) from exc
    converted = result.stdout.strip()
    if result.returncode != 0 or not converted:
        detail = result.stderr.strip() or f"退出代码 {result.returncode}"
        raise WebActionError(
            f"无法转换所选文件路径: {detail}",
            HTTPStatus.BAD_REQUEST,
        )
    return converted


def _expose_picker_environment_to_windows(environment: dict[str, str]) -> None:
    names = (
        "OPEN3D_RECONSTRUCT_PICKER_TITLE",
        "OPEN3D_RECONSTRUCT_PICKER_FILTER",
        "OPEN3D_RECONSTRUCT_PICKER_INITIAL",
    )
    entries = [item for item in environment.get("WSLENV", "").split(":") if item]
    exposed = {item.split("/", 1)[0] for item in entries}
    entries.extend(f"{name}/w" for name in names if name not in exposed)
    environment["WSLENV"] = ":".join(entries)


def _native_file_picker(
    *,
    title: str,
    extensions: tuple[str, ...] = (),
    initial_directory: Path | None = None,
    allow_all: bool = False,
    select_directory: bool = False,
) -> str | None:
    picker_environment: dict[str, str] | None = None
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    use_wsl_picker = sys.platform == "linux" and IS_WSL and powershell is not None
    if sys.platform == "win32" or use_wsl_picker:
        picker = powershell
        if picker is None:  # Native Windows must always have PowerShell available.
            raise WebActionError(
                "系统缺少 Windows PowerShell，无法打开本机文件选择窗口",
                HTTPStatus.NOT_IMPLEMENTED,
            )
        normalized = tuple(item.lstrip(".").lower() for item in extensions)
        if not select_directory and (
            not normalized or any(not item.isalnum() for item in normalized)
        ):
            raise ValueError("Windows 文件类型过滤器无效")
        patterns = ";".join(f"*.{item}" for item in normalized)
        label = "PLY 点云与网格" if normalized == ("ply",) else "录制文件"
        filters = f"{label} ({patterns})|{patterns}" if patterns else ""
        if allow_all and filters:
            filters += "|所有文件 (*.*)|*.*"
        script = (r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$owner = $null
$dialog = $null
$exitCode = 2
try {
    Add-Type -AssemblyName System.Windows.Forms
    $owner = [System.Windows.Forms.Form]::new()
    $owner.Width = 1
    $owner.Height = 1
    $owner.Opacity = 0
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $owner.Show()
    $owner.Activate()

    $dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
    $dialog.Description = $env:OPEN3D_RECONSTRUCT_PICKER_TITLE
    $dialog.ShowNewFolderButton = $false
    if ($env:OPEN3D_RECONSTRUCT_PICKER_INITIAL) {
        $dialog.SelectedPath = $env:OPEN3D_RECONSTRUCT_PICKER_INITIAL
    }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($dialog.SelectedPath)
        $exitCode = 0
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $exitCode = 1
}
finally {
    if ($null -ne $dialog) { $dialog.Dispose() }
    if ($null -ne $owner) { $owner.Close(); $owner.Dispose() }
}
exit $exitCode
""" if select_directory else r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$owner = $null
$dialog = $null
$exitCode = 2
try {
    Add-Type -AssemblyName System.Windows.Forms

    # Keep an invisible topmost owner alive for the lifetime of the common
    # dialog. The dialog remains above the browser even if the browser takes
    # focus again after handling the original button click.
    $owner = [System.Windows.Forms.Form]::new()
    $owner.Width = 1
    $owner.Height = 1
    $owner.Opacity = 0
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $owner.Show()
    $owner.Activate()

    $dialog = [System.Windows.Forms.OpenFileDialog]::new()
    $dialog.Title = $env:OPEN3D_RECONSTRUCT_PICKER_TITLE
    $dialog.Filter = $env:OPEN3D_RECONSTRUCT_PICKER_FILTER
    $dialog.Multiselect = $false
    if ($env:OPEN3D_RECONSTRUCT_PICKER_INITIAL) {
        $dialog.InitialDirectory = $env:OPEN3D_RECONSTRUCT_PICKER_INITIAL
    }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($dialog.FileName)
        $exitCode = 0
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $exitCode = 1
}
finally {
    if ($null -ne $dialog) {
        $dialog.Dispose()
    }
    if ($null -ne $owner) {
        $owner.Close()
        $owner.Dispose()
    }
}
exit $exitCode
""").strip()
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = [
            picker,
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-EncodedCommand",
            encoded,
        ]
        picker_environment = os.environ.copy()
        picker_environment["OPEN3D_RECONSTRUCT_PICKER_TITLE"] = title
        picker_environment["OPEN3D_RECONSTRUCT_PICKER_FILTER"] = filters
        initial_value = str(initial_directory) if initial_directory is not None else ""
        if use_wsl_picker and initial_value:
            initial_value = _translate_wsl_path(initial_value, to_windows=True)
        picker_environment["OPEN3D_RECONSTRUCT_PICKER_INITIAL"] = initial_value
        if use_wsl_picker:
            _expose_picker_environment_to_windows(picker_environment)
        cancel_codes = {2}
    elif sys.platform == "darwin":
        picker = shutil.which("osascript")
        if picker is None:
            raise WebActionError(
                "系统缺少 osascript，无法打开 macOS 文件选择窗口",
                HTTPStatus.NOT_IMPLEMENTED,
            )
        normalized = tuple(item.lstrip(".").lower() for item in extensions)
        if not select_directory and (
            not normalized or any(not item.isalnum() for item in normalized)
        ):
            raise ValueError("macOS 文件类型过滤器无效")
        type_list = ", ".join(f'"{item}"' for item in normalized)
        if select_directory:
            script = (
                "on run argv\n"
                "set promptText to item 1 of argv\n"
                + (
                    "set startFolder to POSIX file (item 2 of argv)\n"
                    "set selectedFolder to choose folder with prompt promptText "
                    "default location startFolder\n"
                    if initial_directory is not None
                    else "set selectedFolder to choose folder with prompt promptText\n"
                )
                + "return POSIX path of selectedFolder\nend run"
            )
        else:
            script = (
            "on run argv\n"
            "set promptText to item 1 of argv\n"
            + (
                "set startFolder to POSIX file (item 2 of argv)\n"
                f"set selectedFile to choose file with prompt promptText of type {{{type_list}}} "
                "default location startFolder\n"
                if initial_directory is not None
                else f"set selectedFile to choose file with prompt promptText of type {{{type_list}}}\n"
            )
                + "return POSIX path of selectedFile\nend run"
            )
        command = [picker, "-e", script, title]
        if initial_directory is not None:
            command.append(str(initial_directory))
        cancel_codes = {1}
    else:
        picker = shutil.which("zenity")
        if picker is None:
            raise WebActionError(
                "系统未安装 zenity，无法打开本机文件选择窗口",
                HTTPStatus.NOT_IMPLEMENTED,
            )
        patterns = " ".join(
            pattern
            for extension in extensions
            for pattern in (f"*.{extension.lower()}", f"*.{extension.upper()}")
        )
        command = [
            picker,
            "--file-selection",
            f"--title={title}",
        ]
        if select_directory:
            command.append("--directory")
        if initial_directory is not None:
            command.append(f"--filename={initial_directory}{os.sep}")
        if not select_directory:
            label = "PLY 点云与网格" if extensions == ("ply",) else "录制文件"
            command.append(f"--file-filter={label} | {patterns}")
            if allow_all:
                command.append("--file-filter=所有文件 | *")
        cancel_codes = {1, 5}
    try:
        run_options: dict[str, Any] = {}
        if picker_environment is not None:
            run_options["env"] = picker_environment
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **run_options,
        )
    except OSError as exc:
        raise WebActionError(
            f"无法打开本机文件选择窗口: {exc}",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        ) from exc
    if result.returncode in cancel_codes:
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"退出代码 {result.returncode}"
        raise WebActionError(
            f"本机文件选择失败: {detail}",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    selected = result.stdout.strip()
    if not selected:
        return None
    if use_wsl_picker:
        return _translate_wsl_path(selected, to_windows=False)
    return selected


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    absolute = Path(os.path.abspath(path))
    try:
        return absolute.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _camera_parameter_display(family: str, key: str, value: object) -> str:
    raw = "" if value is None else str(value).strip()
    mapped = CAMERA_PARAMETER_DISPLAY_VALUES.get((family, key), {}).get(raw)
    if mapped is not None:
        return mapped
    if key == "serial" and not raw:
        return "自动选择页面中的设备"
    if key in {"color_resolution", "depth_resolution"} and "," in raw:
        return raw.replace(",", " × ")
    if key == "fps" and raw:
        return f"{raw} FPS"
    if key in {
        "synchronized_images_only",
        "disable_streaming_indicator",
    }:
        normalized = raw.lower()
        if normalized in {"true", "1"}:
            return "开启"
        if normalized in {"false", "0"}:
            return "关闭"
    if key.endswith("_usec") and raw:
        return f"{raw} µs"
    return raw or "未设置"


def _camera_configuration(hardware_id: str) -> dict[str, Any]:
    spec = HARDWARE.get(hardware_id)
    if spec is None:
        raise ValueError(f"未知相机类型: {hardware_id}")
    family = spec["camera"]
    config_path = (
        DEFAULT_AZURE_SENSOR_CONFIG
        if family == "azure-kinect"
        else DEFAULT_REALSENSE_SENSOR_CONFIG
    )
    try:
        values = read_json_object(config_path)
    except ValueError as exc:
        return {
            "path": _display_path(config_path),
            "parameters": [],
            "error": str(exc),
            "notes": [],
        }

    parameters = []
    for definition in CAMERA_PARAMETER_DEFINITIONS[family]:
        key = definition["key"]
        raw = values.get(key)
        parameters.append(
            {
                **definition,
                "value": raw,
                "display": _camera_parameter_display(family, key, raw),
            }
        )

    notes = [
        "这里展示的是 Web 下次连接录制时读取的配置；查看参数不会打开、停止或重启相机。"
    ]
    if family == "azure-kinect":
        notes.append("Azure Kinect 的 IMU 会被录制和实时显示，但经典 RGB-D 重建不融合 IMU。")
    else:
        notes.append("硬件深度预设由 librealsense 在相机侧应用；录制文件中的实际 depth_scale 会在提取时自动带入重建。")
        if hardware_id == "d435i":
            notes.append("当前 Open3D BAG 录制链路不采集 D435i IMU，D435i 与 D435 使用同一套 RGB-D 配置。")
    return {
        "path": _display_path(config_path),
        "parameters": parameters,
        "error": None,
        "notes": notes,
    }


def _reconstruction_configuration() -> dict[str, Any]:
    catalog = reconstruction_profile_catalog(DEFAULT_RECONSTRUCTION_PROFILES)
    catalog["source"] = _display_path(DEFAULT_RECONSTRUCTION_PROFILES)
    catalog["compute"] = resolve_compute_backend(
        catalog["compute_backend"]
    ).to_dict()
    return catalog


def _file_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return {"path": _display_path(path), "exists": False, "size": 0}
    return {
        "path": _display_path(path),
        "exists": path.is_file(),
        "size": stat.st_size if path.is_file() else 0,
    }


def _is_nonempty_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _path_tree_size(path: Path) -> int:
    """Return logical content size without following directory symlinks."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _path_tree_size(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _clean_recording_name(value: object) -> str:
    if value is None or not str(value).strip():
        return "web-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "-".join(str(value).strip().split())
    lowered = name.lower()
    for suffix in (".mkv", ".bag"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    name = name.strip("._-")
    if not name or len(name) > 64:
        raise WebActionError("录制名称须为 1 到 64 个字符", HTTPStatus.BAD_REQUEST)
    if any(not (character.isalnum() or character in "._-") for character in name):
        raise WebActionError(
            "录制名称只能包含中文或英文字母、数字、点、短横线和下划线",
            HTTPStatus.BAD_REQUEST,
        )
    return name


def _recording_import_spec(
    filename: object, hardware: object = None
) -> tuple[str, str, str]:
    raw_name = str(filename or "").strip()
    if not raw_name:
        raise WebActionError("请选择 MKV 或 BAG 录制文件", HTTPStatus.BAD_REQUEST)
    basename = Path(raw_name).name
    extension = Path(basename).suffix.lower()
    if extension == ".mkv":
        hardware_id = "azure-kinect"
    elif extension == ".bag":
        requested_hardware = str(hardware or "")
        hardware_id = (
            requested_hardware
            if requested_hardware in {"d435", "d435i"}
            else "d435"
        )
    else:
        raise WebActionError(
            "仅支持 Azure Kinect MKV 或 RealSense BAG 录制文件",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )
    return _clean_recording_name(basename), extension, hardware_id


def _validated_reconstruction_parameters(value: object) -> dict[str, Any]:
    try:
        return normalize_reconstruction_parameters(value)
    except ValueError as exc:
        raise WebActionError(str(exc), HTTPStatus.BAD_REQUEST) from exc


class ControlCenter:
    """Owns the single local camera/conversion process used by the web page."""

    def __init__(self, launcher: Path | None = None) -> None:
        prefix, invalid_launcher = _launcher_prefix(launcher)
        self.launcher = (
            launcher.expanduser().resolve()
            if launcher is not None
            else Path(sys.executable).resolve()
        )
        self._launcher_prefix = prefix
        self._invalid_launcher = invalid_launcher
        self._lock = threading.RLock()
        self._device_lock = threading.Lock()
        self._file_picker_lock = threading.Lock()
        self._mesh_preview_lock = threading.Lock()
        self._logs: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_LINES)
        self._revision = 0
        self._generation = 0
        self._process: subprocess.Popen[str] | None = None
        self._task: str | None = None
        self._phase = "idle"
        self._hardware: str | None = None
        self._device = 0
        self._recording: Path | None = None
        self._dataset: Path | None = None
        self._mesh: Path | None = None
        self._loaded_point_cloud: Path | None = None
        self._project: dict[str, Any] | None = None
        self._error: str | None = None
        self._recording_started_at: str | None = None
        self._recording_finished_at: str | None = None
        self._conversion_started_at: str | None = None
        self._conversion_finished_at: str | None = None
        self._conversion_progress: dict[str, Any] | None = None
        self._reconstruction_settings: dict[str, Any] | None = None
        self._conversion_paused_at: str | None = None
        self._conversion_paused_monotonic: float | None = None
        self._suspended_processes: tuple[psutil.Process, ...] = ()
        self._conversion_cancel_requested = False
        self._live_dir: Path | None = None
        self._device_cache: dict[str, Any] | None = None
        self._device_cache_at = 0.0
        self._import_temp: Path | None = None
        self._import_target: Path | None = None
        self._import_expected_bytes = 0
        self._import_received_bytes = 0

    def _touch_locked(self) -> None:
        self._revision += 1

    def _reset_conversion_progress_locked(self) -> None:
        now = time.monotonic()
        self._conversion_progress = {
            "stage": "extract",
            "stage_index": 0,
            "stage_count": len(PIPELINE_STEPS),
            "label": PIPELINE_STEPS[0][1],
            "status": "running",
            "detail": "正在打开录制文件并准备提取帧",
            "processed": 0,
            "total": None,
            "pause_count": 0,
            "paused_total_seconds": 0.0,
            "paused_at": None,
            "settings": dict(self._reconstruction_settings or {}),
            "_started_monotonic": now,
            "_stage_started_monotonic": now,
            "_last_output_monotonic": now,
            "_matching_events": [],
            "_matching_active": {},
            "_matching_seen": set(),
            "_matching_attempted": 0,
            "_matching_succeeded": 0,
            "_matching_failed": 0,
            "_matching_information_max": 0.0,
            "_matching_last": None,
            "_matching_expected": None,
        }

    def _reset_conversion_control_locked(self) -> None:
        self._conversion_paused_at = None
        self._conversion_paused_monotonic = None
        self._suspended_processes = ()
        self._conversion_cancel_requested = False

    @staticmethod
    def _expected_matching_pairs(
        frame_count: int,
        frames_per_fragment: int,
        keyframe_interval: int,
    ) -> int:
        expected = 0
        for first in range(0, frame_count, frames_per_fragment):
            last = min(first + frames_per_fragment, frame_count)
            fragment_frames = last - first
            expected += max(0, fragment_frames - 1)
            keyframes = sum(
                1 for frame in range(first, last)
                if frame % keyframe_interval == 0
            )
            expected += keyframes * (keyframes - 1) // 2
        return expected

    def _update_matching_plan_locked(
        self, progress: dict[str, Any], frame_count: int
    ) -> None:
        settings = progress.get("settings") or {}
        frames_per_fragment = max(
            1, int(settings.get("n_frames_per_fragment", 100))
        )
        keyframe_interval = max(
            1, int(settings.get("n_keyframes_per_n_frame", 5))
        )
        progress["_matching_expected"] = self._expected_matching_pairs(
            frame_count,
            frames_per_fragment,
            keyframe_interval,
        )

    def _observe_matching_event_locked(
        self, progress: dict[str, Any], message: str
    ) -> bool:
        if not message.startswith(WEB_MATCH_PREFIX):
            return False
        try:
            payload = json.loads(message[len(WEB_MATCH_PREFIX):])
        except (json.JSONDecodeError, TypeError):
            return True
        if not isinstance(payload, dict):
            return True

        status = payload.get("status")
        kind = payload.get("kind")
        if status not in {"running", "result"} or kind not in {"odometry", "loop"}:
            return True
        indices: dict[str, int] = {}
        for field in ("fragment", "source", "target"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return True
            indices[field] = value
        if indices["target"] <= indices["source"]:
            return True

        key = (
            f"{indices['fragment']}:{indices['source']}:"
            f"{indices['target']}:{kind}"
        )
        event: dict[str, Any] = {
            **indices,
            "kind": kind,
        }
        active = progress.setdefault("_matching_active", {})
        if status == "running":
            active[key] = event
            progress["preview_frame"] = indices["target"]
            progress["fragment_current"] = indices["fragment"] + 1
            progress["detail"] = (
                f"片段 {indices['fragment'] + 1}/"
                f"{progress.get('fragment_total') or '—'}：正在估计帧 "
                f"{indices['source']} ↔ {indices['target']} 的位姿"
            )
            return True

        active.pop(key, None)
        seen = progress.setdefault("_matching_seen", set())
        if key in seen:
            return True
        seen.add(key)
        success = payload.get("success") is True

        def finite_metric(name: str) -> float:
            raw = payload.get(name, 0.0)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return 0.0
            value = float(raw)
            return max(0.0, value) if math.isfinite(value) else 0.0

        event.update(
            success=success,
            information=finite_metric("information") if success else 0.0,
            translation_m=finite_metric("translation_m") if success else 0.0,
            rotation_deg=finite_metric("rotation_deg") if success else 0.0,
        )
        events = progress.setdefault("_matching_events", [])
        events.append(event)
        if len(events) > MAX_MATCHING_EVENTS:
            del events[:len(events) - MAX_MATCHING_EVENTS]
        progress["_matching_attempted"] = int(
            progress.get("_matching_attempted") or 0
        ) + 1
        outcome_key = "_matching_succeeded" if success else "_matching_failed"
        progress[outcome_key] = int(progress.get(outcome_key) or 0) + 1
        progress["_matching_information_max"] = max(
            float(progress.get("_matching_information_max") or 0.0),
            event["information"],
        )
        progress["_matching_last"] = event
        progress["preview_frame"] = indices["target"]
        progress["fragment_current"] = indices["fragment"] + 1
        expected = progress.get("_matching_expected")
        completed_text = (
            f"{progress['_matching_attempted']}/{expected}"
            if isinstance(expected, int) and expected > 0
            else str(progress["_matching_attempted"])
        )
        outcome = "成功" if success else "失败"
        progress["detail"] = (
            f"已计算 {completed_text} 组帧对；帧 "
            f"{indices['source']} ↔ {indices['target']} 匹配{outcome}"
        )
        return True

    def _observe_conversion_output_locked(self, message: str) -> None:
        progress = self._conversion_progress
        if self._task != "convert" or progress is None:
            return

        now = time.monotonic()
        progress["_last_output_monotonic"] = now

        if self._observe_matching_event_locked(progress, message):
            return

        extracted = re.search(r"已提取\s+(\d+)\s+帧", message)
        if extracted:
            count = int(extracted.group(1))
            progress.update(
                stage="extract",
                stage_index=0,
                label=PIPELINE_STEPS[0][1],
                detail=f"已提取 {count} 组对齐 RGB-D 帧",
                processed=count,
            )
            return

        extraction_done = re.search(r"提取完成.+（(\d+)\s+帧", message)
        if extraction_done:
            count = int(extraction_done.group(1))
            progress.update(
                stage="extract",
                stage_index=0,
                label=PIPELINE_STEPS[0][1],
                detail=f"RGB-D 提取完成，共 {count} 帧",
                processed=count,
                total=count,
            )
            return

        dataset_ready = re.search(r"数据集检查通过：(\d+)\s+帧", message)
        if dataset_ready:
            count = int(dataset_ready.group(1))
            progress["frame_count"] = count
            progress["detail"] = f"数据集检查通过，共 {count} 帧"
            settings = progress.get("settings") or {}
            frames_per_fragment = int(settings.get("n_frames_per_fragment", 100))
            progress["fragment_total"] = max(1, math.ceil(count / frames_per_fragment))
            self._update_matching_plan_locked(progress, count)
            return

        stage_header = re.search(r"\[(\d+)/(\d+)\]\s+(.+?)\s*$", message)
        if stage_header:
            label = stage_header.group(3)
            key = PIPELINE_LABEL_TO_KEY.get(label)
            if key is not None:
                stage_index = next(
                    index for index, (candidate, _label) in enumerate(PIPELINE_STEPS)
                    if candidate == key
                )
                progress.update(
                    stage=key,
                    stage_index=stage_index,
                    label=label,
                    detail=f"正在执行：{label}",
                    processed=0,
                    total=None,
                    _stage_started_monotonic=now,
                )
            return

        fragment_plan = re.search(
            r"局部片段计划：\s*(\d+)\s*帧，\s*(\d+)\s*个片段，"
            r"使用\s*(\d+)\s*个并行进程",
            message,
        )
        if fragment_plan:
            frame_count = int(fragment_plan.group(1))
            fragment_total = int(fragment_plan.group(2))
            workers = int(fragment_plan.group(3))
            progress.update(
                stage="make",
                stage_index=1,
                label=PIPELINE_STEPS[1][1],
                detail=(
                    f"已分配 {fragment_total} 个局部片段，"
                    f"{workers} 个进程并行计算"
                ),
                frame_count=frame_count,
                fragment_total=fragment_total,
                fragment_completed=0,
                worker_count=workers,
                processed=0,
                total=fragment_total,
            )
            self._update_matching_plan_locked(progress, frame_count)
            return

        fragment_started = re.search(
            r"片段\s+(\d+)\s*/\s*(\d+)\s+开始：帧\s+(\d+)\s*[—-]\s*(\d+)",
            message,
        )
        if fragment_started and progress.get("stage") == "make":
            current = int(fragment_started.group(1))
            total = int(fragment_started.group(2))
            first_frame = int(fragment_started.group(3))
            last_frame = int(fragment_started.group(4))
            progress.update(
                fragment_current=current,
                fragment_total=total,
                preview_frame=first_frame,
                detail=(
                    f"片段 {current}/{total} 已开始，"
                    f"正在处理帧 {first_frame}–{last_frame}"
                ),
            )
            return

        fragment_done = re.search(
            r"片段完成\s+(\d+)\s*/\s*(\d+)（片段\s+(\d+)）",
            message,
        )
        if fragment_done and progress.get("stage") == "make":
            completed = int(fragment_done.group(1))
            total = int(fragment_done.group(2))
            fragment_id = int(fragment_done.group(3))
            progress.update(
                fragment_completed=completed,
                fragment_total=total,
                processed=completed,
                total=total,
                detail=(
                    f"局部片段已完成 {completed}/{total}"
                    f"（刚完成片段 {fragment_id}）"
                ),
            )
            return

        if message.startswith("making fragments from RGBD sequence"):
            fragment_total = progress.get("fragment_total")
            progress.update(
                stage="make",
                stage_index=1,
                label=PIPELINE_STEPS[1][1],
                detail=(
                    f"正在启动局部片段计算，共 {fragment_total} 个片段"
                    if isinstance(fragment_total, int)
                    else (
                        "正在启动局部片段计算；"
                        "首次启动子进程可能需要一些时间"
                    )
                ),
                processed=0,
                total=fragment_total,
            )
            return

        integration = re.search(
            r"(?:Fragment\s+(\d+)\s+/\s+(\d+)\s+::\s+)?"
            r"integrate rgbd frame\s+(\d+)\s+\((\d+) of (\d+)\)",
            message,
        )
        if integration:
            fragment_index = integration.group(1)
            fragment_last = integration.group(2)
            frame_index = int(integration.group(3))
            local_current = int(integration.group(4))
            local_total = int(integration.group(5))
            progress["preview_frame"] = frame_index
            if progress.get("stage") == "make" and fragment_index is not None:
                progress["detail"] = (
                    f"片段 {int(fragment_index) + 1}/{int(fragment_last) + 1}："
                    f"正在融合帧 {frame_index}（{local_current}/{local_total}）"
                )
            else:
                progress["detail"] = (
                    f"正在融合 RGB-D 帧 {frame_index}（当前片段 "
                    f"{local_current}/{local_total}）"
                )
            frame_count = progress.get("frame_count")
            if progress.get("stage") == "integrate" and isinstance(frame_count, int):
                progress["processed"] = min(frame_index + 1, frame_count)
                progress["total"] = frame_count
            return

        fragment_match = re.search(
            r"Fragment\s+(\d+)\s+/\s+(\d+)\s+::\s+"
            r"RGBD matching between frame\s*:\s*(\d+) and (\d+)",
            message,
        )
        if fragment_match and progress.get("stage") == "make":
            progress["preview_frame"] = int(fragment_match.group(4))
            progress["fragment_current"] = int(fragment_match.group(1)) + 1
            progress["fragment_total"] = int(fragment_match.group(2)) + 1
            progress["detail"] = (
                f"片段 {int(fragment_match.group(1)) + 1}/"
                f"{int(fragment_match.group(2)) + 1}：匹配帧 "
                f"{fragment_match.group(3)} ↔ {fragment_match.group(4)}"
            )
            return

        for key, label in PIPELINE_STEPS[1:]:
            if message.startswith(f"{label}完成，用时"):
                progress["detail"] = message
                return

    def _append_log(self, message: str, *, level: str = "info") -> None:
        message = message.rstrip("\r\n")
        if not message:
            return
        if len(message) > 8000:
            message = message[:8000] + "…"
        with self._lock:
            self._observe_conversion_output_locked(message)
            if not message.startswith(WEB_MATCH_PREFIX):
                self._logs.append(
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "level": level,
                        "message": message,
                    }
                )
            self._touch_locked()

    def _new_paths_locked(self, requested_name: object, extension: str) -> tuple[Path, Path]:
        root = ROOT.resolve()
        for storage in (RECORDINGS_DIR, DATASETS_DIR):
            try:
                storage.resolve().relative_to(root)
            except ValueError as exc:
                raise WebActionError(
                    f"拒绝使用项目目录外的数据路径: {storage}",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc
        base = _clean_recording_name(requested_name)
        candidate = base
        suffix = 1
        while True:
            recording = RECORDINGS_DIR / f"{candidate}{extension}"
            dataset = DATASETS_DIR / candidate
            partials = tuple(DATASETS_DIR.glob(f".{candidate}.partial-*"))
            if not os.path.lexists(recording) and not dataset.exists() and not partials:
                return recording.resolve(), dataset.resolve()
            suffix += 1
            candidate = f"{base}-{suffix}"

    def _discard_live_dir_locked(self) -> None:
        directory = self._live_dir
        self._live_dir = None
        if directory is None:
            return
        try:
            directory.resolve().relative_to(RUNTIME_DIR.resolve())
        except ValueError:
            return
        shutil.rmtree(directory, ignore_errors=True)

    def _prepare_live_dir_locked(self) -> Path:
        self._discard_live_dir_locked()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(prefix="web-live-", dir=RUNTIME_DIR)
        ).resolve()
        self._live_dir = directory
        return directory

    def _spawn_locked(
        self,
        task: str,
        command: list[str],
        *,
        environment_overrides: dict[str, str] | None = None,
    ) -> None:
        if self._process is not None:
            raise WebActionError("已有任务正在运行，请等待它结束")
        if self._invalid_launcher is not None or not self._launcher_prefix:
            invalid = self._invalid_launcher or self.launcher
            raise WebActionError(f"项目启动器不可执行: {invalid}", HTTPStatus.INTERNAL_SERVER_ERROR)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        if environment_overrides:
            environment.update(environment_overrides)
        self._generation += 1
        generation = self._generation
        self._append_log("$ " + _format_command(command), level="command")
        try:
            process_options: dict[str, Any] = {}
            if os.name == "nt":
                process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                process_options["start_new_session"] = True
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                close_fds=True,
                **process_options,
            )
        except OSError as exc:
            raise WebActionError(f"无法启动任务: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR) from exc

        self._process = process
        self._task = task
        self._touch_locked()
        threading.Thread(
            target=self._read_process_output,
            args=(process,),
            name=f"web-{task}-output",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._wait_for_process,
            args=(process, task, generation),
            name=f"web-{task}-wait",
            daemon=True,
        ).start()

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                self._append_log(line)
        finally:
            stream.close()

    def _wait_for_process(
        self, process: subprocess.Popen[str], task: str, generation: int
    ) -> None:
        returncode = process.wait()
        with self._lock:
            if self._process is not process or self._generation != generation:
                return
            self._process = None
            self._task = None
            if task == "record":
                self._finish_recording_locked(returncode)
            else:
                self._finish_conversion_locked(returncode)
            self._touch_locked()

    def _finish_recording_locked(self, returncode: int) -> None:
        self._recording_finished_at = _now_iso()
        recording = self._recording
        valid = _is_nonempty_file(recording)
        if returncode == 0 and valid:
            self._phase = "recorded"
            self._error = None
            self._append_log("录制文件已封装完成，可以开始重建。", level="success")
            return
        self._phase = "error"
        if valid:
            self._error = f"录制进程异常退出（代码 {returncode}）；文件已保留，请查看日志"
        else:
            self._error = f"录制失败（退出代码 {returncode}），没有生成有效录制文件"
        self._append_log(self._error, level="error")

    def begin_recording_import(
        self,
        *,
        filename: object,
        hardware: object,
        size: int,
    ) -> tuple[int, Path]:
        if size <= 0:
            raise WebActionError("录制文件为空", HTTPStatus.BAD_REQUEST)
        if size > MAX_RECORDING_UPLOAD_BYTES:
            raise WebActionError(
                "录制文件超过 256 GiB 导入上限",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        base, extension, hardware_id = _recording_import_spec(filename, hardware)
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
            recording, dataset = self._new_paths_locked(base, extension)
            target_fd: int | None = None
            temporary_fd: int | None = None
            temporary_path: Path | None = None
            try:
                target_fd = os.open(
                    recording,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.close(target_fd)
                target_fd = None
                temporary_fd, temporary_name = tempfile.mkstemp(
                    prefix=f".{recording.stem}.importing-",
                    suffix=extension,
                    dir=RECORDINGS_DIR,
                )
                os.close(temporary_fd)
                temporary_fd = None
                temporary_path = Path(temporary_name).resolve()
            except OSError as exc:
                if target_fd is not None:
                    os.close(target_fd)
                if temporary_fd is not None:
                    os.close(temporary_fd)
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
                recording.unlink(missing_ok=True)
                raise WebActionError(
                    f"无法准备录制文件导入: {exc}",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            self._generation += 1
            token = self._generation
            self._logs.clear()
            self._task = "import"
            self._phase = "importing"
            self._hardware = hardware_id
            self._device = 0
            self._recording = recording
            self._dataset = dataset
            self._mesh = dataset / "scene" / "integrated.ply"
            self._loaded_point_cloud = None
            self._project = None
            self._error = None
            self._recording_started_at = None
            self._recording_finished_at = None
            self._conversion_started_at = None
            self._conversion_finished_at = None
            self._conversion_progress = None
            self._reconstruction_settings = None
            self._import_temp = temporary_path
            self._import_target = recording
            self._import_expected_bytes = size
            self._import_received_bytes = 0
            self._discard_live_dir_locked()
            self._append_log(
                f"正在导入已有录制：{Path(str(filename)).name}（{size} 字节）。",
                level="command",
            )
            self._touch_locked()
            return token, temporary_path

    def update_recording_import(self, token: int, received: int) -> None:
        with self._lock:
            if token != self._generation or self._task != "import":
                raise WebActionError("录制文件导入任务已失效")
            self._import_received_bytes = min(
                max(0, int(received)), self._import_expected_bytes
            )
            self._touch_locked()

    def finish_recording_import(self, token: int) -> dict[str, Any]:
        with self._lock:
            if token != self._generation or self._task != "import":
                raise WebActionError("录制文件导入任务已失效")
            temporary = self._import_temp
            target = self._import_target
            expected = self._import_expected_bytes
            if temporary is None or target is None:
                raise WebActionError(
                    "录制文件导入状态不完整",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            try:
                actual = temporary.stat().st_size
            except OSError as exc:
                raise WebActionError(
                    f"无法检查导入文件: {exc}",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc
            if actual != expected or self._import_received_bytes != expected:
                raise WebActionError(
                    f"录制文件接收不完整：{actual}/{expected} 字节",
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                os.replace(temporary, target)
            except OSError as exc:
                raise WebActionError(
                    f"无法保存导入的录制文件: {exc}",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            self._import_temp = None
            self._import_target = None
            self._import_received_bytes = expected
            self._task = None
            self._phase = "recorded"
            self._recording_finished_at = _now_iso()
            self._error = None
            self._append_log(
                f"已有录制导入完成：{_display_path(target)}。可以开始重建。",
                level="success",
            )
            self._touch_locked()
            return self._snapshot_locked()

    def abort_recording_import(self, token: int, message: str) -> None:
        with self._lock:
            if token != self._generation or self._task != "import":
                return
            temporary = self._import_temp
            target = self._import_target
            self._import_temp = None
            self._import_target = None
            self._import_expected_bytes = 0
            self._import_received_bytes = 0
            self._task = None
            self._phase = "error"
            self._recording = None
            self._dataset = None
            self._mesh = None
            cleanup_errors: list[str] = []
            for path in (temporary, target):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        cleanup_errors.append(f"{path.name}: {exc}")
            if cleanup_errors:
                message = f"{message}；临时文件清理失败：{'；'.join(cleanup_errors)}"
            self._error = message
            self._append_log(message, level="error")
            self._touch_locked()

    @staticmethod
    def _recording_storage_mode(path: Path) -> str:
        if path.is_symlink():
            return "symlink"
        try:
            return "hardlink" if path.stat().st_nlink > 1 else "owned"
        except OSError:
            return "owned"

    @staticmethod
    def _managed_recording_path(name: object, *, require_exists: bool = True) -> Path:
        raw_name = str(name or "").strip()
        if not raw_name or raw_name != Path(raw_name).name:
            raise WebActionError("本地录制名称无效", HTTPStatus.BAD_REQUEST)
        if Path(raw_name).suffix.lower() not in {".mkv", ".bag"}:
            raise WebActionError("仅支持管理 MKV 或 BAG 录制", HTTPStatus.BAD_REQUEST)
        path = RECORDINGS_DIR / raw_name
        if Path(os.path.abspath(path)).parent != RECORDINGS_DIR.resolve():
            raise WebActionError("本地录制路径越界", HTTPStatus.BAD_REQUEST)
        if require_exists and not os.path.lexists(path):
            raise WebActionError("本地录制不存在或已经删除", HTTPStatus.NOT_FOUND)
        return path.absolute()

    @staticmethod
    def _dataset_artifact_paths(recording_name: str) -> tuple[Path, list[Path]]:
        stem = Path(recording_name).stem
        dataset = Path(os.path.abspath(DATASETS_DIR / stem))
        if dataset.parent != DATASETS_DIR.resolve():
            raise WebActionError("关联数据集路径越界", HTTPStatus.BAD_REQUEST)
        candidates = [dataset, dataset.with_name(dataset.name + ".extracting")]
        partial_prefix = f".{stem}.partial-"
        try:
            candidates.extend(
                path.absolute()
                for path in DATASETS_DIR.iterdir()
                if path.name.startswith(partial_prefix)
            )
        except FileNotFoundError:
            pass
        artifacts = [path for path in candidates if os.path.lexists(path)]
        return dataset, artifacts

    def _activate_recording_locked(
        self,
        recording: Path,
        dataset: Path,
        hardware: str,
        *,
        message: str,
    ) -> dict[str, Any]:
        if self._process is not None or self._task is not None:
            raise WebActionError("已有任务正在运行，请等待它结束")
        if not _is_nonempty_file(recording):
            raise WebActionError("所选录制文件为空或不可读", HTTPStatus.BAD_REQUEST)

        self._generation += 1
        self._logs.clear()
        self._phase = "recorded"
        self._hardware = hardware
        self._device = 0
        self._recording = recording.absolute()
        self._dataset = dataset.absolute()
        self._mesh = self._dataset / "scene" / "integrated.ply"
        self._loaded_point_cloud = None
        self._project = None
        self._error = None
        self._recording_started_at = None
        try:
            modified = recording.stat().st_mtime
            self._recording_finished_at = datetime.fromtimestamp(
                modified
            ).astimezone().isoformat(timespec="seconds")
        except OSError:
            self._recording_finished_at = _now_iso()
        self._conversion_started_at = None
        self._conversion_finished_at = None
        self._conversion_progress = None
        self._reconstruction_settings = None
        self._reset_conversion_control_locked()
        self._import_temp = None
        self._import_target = None
        self._import_expected_bytes = 0
        self._import_received_bytes = 0
        self._discard_live_dir_locked()
        if _is_nonempty_file(self._mesh):
            self._phase = "completed"
            try:
                completed = self._mesh.stat().st_mtime
                self._conversion_finished_at = datetime.fromtimestamp(
                    completed
                ).astimezone().isoformat(timespec="seconds")
            except OSError:
                self._conversion_finished_at = None
        self._append_log(message, level="success")
        if self._phase == "completed":
            self._append_log(
                "已发现关联重建结果，模型预览可以直接使用。",
                level="success",
            )
        self._touch_locked()
        return self._snapshot_locked()

    def reference_local_recording(
        self, path: object, *, hardware: object = None
    ) -> dict[str, Any]:
        raw_path = str(path or "").strip()
        if not raw_path:
            raise WebActionError("未选择录制文件", HTTPStatus.BAD_REQUEST)
        try:
            source = Path(raw_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise WebActionError(
                f"无法访问所选录制文件: {exc}", HTTPStatus.BAD_REQUEST
            ) from exc
        if not source.is_file() or source.stat().st_size <= 0:
            raise WebActionError("所选录制文件为空或不可读", HTTPStatus.BAD_REQUEST)
        base, extension, hardware_id = _recording_import_spec(source.name, hardware)
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)

        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
            managed: Path | None = None
            try:
                entries = tuple(RECORDINGS_DIR.iterdir())
            except FileNotFoundError:
                entries = ()
            for candidate in entries:
                if candidate.suffix.lower() != extension:
                    continue
                try:
                    if candidate.is_file() and os.path.samefile(candidate, source):
                        managed = candidate.absolute()
                        break
                except OSError:
                    continue

            created = False
            if managed is None:
                managed, dataset = self._new_paths_locked(base, extension)
                try:
                    os.link(source, managed)
                    mode = "hardlink"
                except OSError as hardlink_error:
                    try:
                        managed.symlink_to(source)
                        mode = "symlink"
                    except OSError as symlink_error:
                        hint = (
                            "；Windows 跨卷零拷贝需要启用开发人员模式，"
                            "或将录制文件放到项目所在的同一 NTFS 卷"
                            if os.name == "nt"
                            else ""
                        )
                        raise WebActionError(
                            "无法建立录制文件零拷贝引用："
                            f"硬链接失败（{hardlink_error}）；"
                            f"符号引用失败（{symlink_error}）{hint}",
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                        ) from symlink_error
                created = True
            else:
                dataset, _ = self._dataset_artifact_paths(managed.name)
                mode = self._recording_storage_mode(managed)

            labels = {
                "hardlink": "硬链接",
                "symlink": "符号引用",
                "owned": "已有项目文件",
            }
            try:
                return self._activate_recording_locked(
                    managed,
                    dataset,
                    hardware_id,
                    message=(
                        f"已通过{labels[mode]}引用本地录制，不复制视频数据："
                        f"{_display_path(managed)}。"
                    ),
                )
            except Exception:
                if created:
                    managed.unlink(missing_ok=True)
                raise

    def choose_local_recording(
        self, *, hardware: object = None
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
        if not self._file_picker_lock.acquire(blocking=False):
            raise WebActionError("本地文件选择窗口已经打开")
        try:
            selected = _native_file_picker(
                title="选择 Azure Kinect MKV 或 RealSense BAG",
                extensions=("mkv", "bag"),
                allow_all=True,
            )
            if selected is None:
                return None
            return self.reference_local_recording(selected, hardware=hardware)
        finally:
            self._file_picker_lock.release()

    @staticmethod
    def _project_summary(
        manifest: dict[str, Any], path: Path, *, opened: bool
    ) -> dict[str, Any]:
        reconstruction = manifest.get("reconstruction")
        if not isinstance(reconstruction, dict):
            reconstruction = {}
        analysis = manifest.get("web_analysis")
        if not isinstance(analysis, dict):
            analysis = {}
        matching = analysis.get("matching")
        if not isinstance(matching, dict):
            matching = {}
        compute = reconstruction.get("compute")
        if not isinstance(compute, dict):
            compute = {}
        return {
            "path": _display_path(path),
            "filename": path.name,
            "opened": opened,
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "format_version": manifest.get("format_version"),
            "frame_count": reconstruction.get("frame_count"),
            "stages": list(reconstruction.get("stages") or []),
            "timings_seconds": dict(reconstruction.get("timings_seconds") or {}),
            "total_seconds": reconstruction.get("total_seconds"),
            "compute": compute,
            "settings": dict(reconstruction.get("settings") or {}),
            "matching": {
                key: matching.get(key)
                for key in ("expected", "attempted", "succeeded", "failed")
            },
            "artifact_count": len(manifest.get("artifacts") or {}),
        }

    def _persist_completed_project_locked(self) -> None:
        dataset = self._dataset
        if dataset is None or not dataset.is_dir() or not _is_nonempty_file(self._mesh):
            return
        try:
            manifest = write_reconstruction_project(
                dataset,
                recording=self._recording,
                hardware=self._hardware,
                started_at=self._conversion_started_at,
                finished_at=self._conversion_finished_at,
                settings=self._reconstruction_settings,
                conversion=self._process_snapshot_locked(),
                logs=list(self._logs),
            )
            project_path = dataset / PROJECT_FILENAME
            self._project = self._project_summary(
                manifest, project_path, opened=False
            )
            self._append_log(
                f"重建工程配置已保存：{_display_path(project_path)}。",
                level="success",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._project = None
            self._append_log(
                f"最终模型已生成，但工程配置保存失败：{exc}",
                level="error",
            )

    def open_project(self, path: object) -> dict[str, Any]:
        raw_path = str(path or "").strip()
        if not raw_path:
            raise WebActionError("未选择重建工程目录", HTTPStatus.BAD_REQUEST)
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
        try:
            loaded = load_reconstruction_project(Path(raw_path))
        except (OSError, RuntimeError, ValueError) as exc:
            raise WebActionError(
                f"无法打开重建工程：{exc}", HTTPStatus.BAD_REQUEST
            ) from exc

        reconstruction = loaded["reconstruction"]
        analysis = dict(loaded["analysis"])
        matching = analysis.pop("matching", {})
        now = time.monotonic()
        elapsed = max(0.0, float(analysis.get("elapsed_seconds") or 0.0))
        stage_elapsed = max(
            0.0, float(analysis.get("stage_elapsed_seconds") or elapsed)
        )
        analysis.update(
            stage="complete",
            stage_index=len(PIPELINE_STEPS),
            stage_count=len(PIPELINE_STEPS),
            label="重建完成",
            status="completed",
            detail="已从工程配置恢复重建结果与分析数据",
            frame_count=(
                analysis.get("frame_count") or reconstruction.get("frame_count")
            ),
            settings=dict(reconstruction.get("settings") or {}),
            _started_monotonic=now - elapsed,
            _stage_started_monotonic=now - stage_elapsed,
            _last_output_monotonic=now,
            _finished_monotonic=now,
            _matching_events=list(
                (matching.get("events") or [])
                if isinstance(matching, dict)
                else []
            ),
            _matching_active={},
            _matching_seen=set(),
            _matching_attempted=int(
                matching.get("attempted") or 0
                if isinstance(matching, dict)
                else 0
            ),
            _matching_succeeded=int(
                matching.get("succeeded") or 0
                if isinstance(matching, dict)
                else 0
            ),
            _matching_failed=int(
                matching.get("failed") or 0
                if isinstance(matching, dict)
                else 0
            ),
            _matching_information_max=float(
                matching.get("information_max") or 0.0
                if isinstance(matching, dict)
                else 0.0
            ),
            _matching_last=(
                matching.get("last") if isinstance(matching, dict) else None
            ),
            _matching_expected=(
                matching.get("expected") if isinstance(matching, dict) else None
            ),
        )
        hardware = str(loaded.get("hardware") or "")
        recording = loaded.get("recording")
        if hardware not in HARDWARE:
            if isinstance(recording, Path) and recording.suffix.lower() == ".mkv":
                hardware = "azure-kinect"
            elif isinstance(recording, Path) and recording.suffix.lower() == ".bag":
                hardware = "d435"
            else:
                hardware = ""

        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
            self._generation += 1
            self._logs.clear()
            for item in loaded["logs"]:
                self._logs.append(dict(item))
            self._phase = "completed"
            self._hardware = hardware or None
            self._device = 0
            self._recording = recording
            self._dataset = loaded["dataset"]
            self._mesh = loaded["mesh"]
            self._loaded_point_cloud = None
            self._error = None
            self._recording_started_at = None
            self._recording_finished_at = None
            self._conversion_started_at = reconstruction.get("started_at")
            self._conversion_finished_at = reconstruction.get("finished_at")
            self._conversion_progress = analysis
            self._reconstruction_settings = dict(
                reconstruction.get("settings") or {}
            )
            self._reset_conversion_control_locked()
            self._project = self._project_summary(
                loaded["manifest"], loaded["path"], opened=True
            )
            self._discard_live_dir_locked()
            self._append_log(
                f"已打开重建工程：{_display_path(loaded['dataset'])}。",
                level="success",
            )
            self._touch_locked()
            return self._snapshot_locked()

    def choose_local_project(self) -> dict[str, Any] | None:
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
            initial_directory = self._dataset if self._dataset else DATASETS_DIR
        if not self._file_picker_lock.acquire(blocking=False):
            raise WebActionError("本地文件选择窗口已经打开")
        try:
            selected = _native_file_picker(
                title="选择 Open3D 重建工程目录",
                initial_directory=initial_directory,
                select_directory=True,
            )
            if selected is None:
                return None
            return self.open_project(selected)
        finally:
            self._file_picker_lock.release()

    def _point_cloud_picker_directory(self) -> Path:
        with self._lock:
            mesh = self._mesh
            dataset = self._dataset
        candidates = []
        if mesh is not None:
            candidates.append(mesh.parent)
        if dataset is not None:
            candidates.append(dataset / "scene")
        candidates.append(DATASETS_DIR)
        for candidate in candidates:
            try:
                if candidate.is_dir():
                    return candidate.resolve()
            except OSError:
                continue
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        return DATASETS_DIR.resolve()

    @staticmethod
    def _validated_local_point_cloud(path: object) -> Path:
        raw_path = str(path or "").strip()
        if not raw_path:
            raise WebActionError("未选择点云文件", HTTPStatus.BAD_REQUEST)
        if Path(raw_path).suffix.lower() not in POINT_CLOUD_SUFFIXES:
            raise WebActionError(
                "仅支持 .ply 点云文件",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
        try:
            source = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WebActionError(
                f"所选点云文件不存在或不可访问: {exc}",
                HTTPStatus.BAD_REQUEST,
            ) from exc
        if not _is_nonempty_file(source):
            raise WebActionError("所选点云文件为空或不可读", HTTPStatus.BAD_REQUEST)
        try:
            with source.open("rb") as stream:
                signature = stream.read(4)
        except OSError as exc:
            raise WebActionError(
                f"无法读取所选点云文件: {exc}",
                HTTPStatus.BAD_REQUEST,
            ) from exc
        if signature not in {b"ply\n", b"ply\r"}:
            raise WebActionError(
                "文件后缀是 .ply，但文件内容不是有效的 PLY 格式",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
        return source

    def select_local_point_cloud(self, path: object) -> dict[str, Any]:
        source = self._validated_local_point_cloud(path)
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
            self._loaded_point_cloud = source
            self._append_log(
                "已加载本地点云用于浏览器预览（不会复制原文件）："
                f"{_display_path(source)}。",
                level="success",
            )
            return self._snapshot_locked()

    def choose_local_point_cloud(self) -> dict[str, Any] | None:
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("已有任务正在运行，请等待它结束")
        if not self._file_picker_lock.acquire(blocking=False):
            raise WebActionError("本地文件选择窗口已经打开")
        try:
            initial_directory = self._point_cloud_picker_directory()
            selected = _native_file_picker(
                title="选择 PLY 点云文件",
                extensions=("ply",),
                initial_directory=initial_directory,
            )
            if selected is None:
                return None
            return self.select_local_point_cloud(selected)
        finally:
            self._file_picker_lock.release()

    def clear_local_point_cloud(self) -> dict[str, Any]:
        with self._lock:
            if self._loaded_point_cloud is not None:
                self._loaded_point_cloud = None
                self._append_log("已关闭外部点云预览。", level="success")
            return self._snapshot_locked()

    def select_managed_recording(
        self, name: object, *, hardware: object = None
    ) -> dict[str, Any]:
        recording = self._managed_recording_path(name)
        _, _, hardware_id = _recording_import_spec(recording.name, hardware)
        dataset, _ = self._dataset_artifact_paths(recording.name)
        with self._lock:
            return self._activate_recording_locked(
                recording,
                dataset,
                hardware_id,
                message=f"已选择本地录制：{_display_path(recording)}。",
            )

    def recordings_snapshot(self) -> dict[str, Any]:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            busy = self._process is not None or self._task is not None
            active = self._recording.absolute() if self._recording else None

        items: list[dict[str, Any]] = []
        for recording in RECORDINGS_DIR.iterdir():
            if recording.suffix.lower() not in {".mkv", ".bag"}:
                continue
            exists = _is_nonempty_file(recording)
            try:
                stat = recording.stat()
                size = stat.st_size if exists else 0
                modified_at = datetime.fromtimestamp(
                    stat.st_mtime
                ).astimezone().isoformat(timespec="seconds")
            except OSError:
                size = 0
                modified_at = None
            mode = self._recording_storage_mode(recording)
            try:
                dataset, artifact_paths = self._dataset_artifact_paths(
                    recording.name
                )
            except WebActionError:
                dataset = DATASETS_DIR / "invalid"
                artifact_paths = []
            output_size = sum(_path_tree_size(path) for path in artifact_paths)
            mesh = dataset / "scene" / "integrated.ply"
            source_path = None
            if recording.is_symlink():
                try:
                    source_path = str(recording.resolve(strict=False))
                except OSError:
                    source_path = str(recording.readlink())
            items.append(
                {
                    "name": recording.name,
                    "path": recording.absolute().relative_to(ROOT.resolve()).as_posix(),
                    "hardware": (
                        "azure-kinect"
                        if recording.suffix.lower() == ".mkv"
                        else "d435"
                    ),
                    "exists": exists,
                    "size": size,
                    "modified_at": modified_at,
                    "storage_mode": mode,
                    "source_path": source_path,
                    "additional_bytes": 0 if mode in {"hardlink", "symlink"} else size,
                    "active": active == recording.absolute(),
                    "can_use": exists and not busy,
                    "can_delete": not busy,
                    "dataset": {
                        "path": dataset.absolute().relative_to(ROOT.resolve()).as_posix(),
                        "exists": bool(artifact_paths),
                        "size": output_size,
                        "has_preprocessed": any(
                            (path / "color").is_dir() or (path / "depth").is_dir()
                            for path in artifact_paths
                            if path.is_dir()
                        ),
                        "mesh_exists": _is_nonempty_file(mesh),
                        "mesh_size": mesh.stat().st_size if _is_nonempty_file(mesh) else 0,
                    },
                }
            )
        items.sort(
            key=lambda item: (item["modified_at"] or "", item["name"]),
            reverse=True,
        )
        return {
            "items": items,
            "busy": busy,
            "totals": {
                "count": len(items),
                "recording_bytes": sum(item["size"] for item in items),
                "additional_bytes": sum(item["additional_bytes"] for item in items),
                "output_bytes": sum(item["dataset"]["size"] for item in items),
            },
        }

    def delete_managed_recording(
        self, name: object, *, delete_outputs: object = False
    ) -> dict[str, Any]:
        if not isinstance(delete_outputs, bool):
            raise WebActionError("删除产物选项必须是布尔值", HTTPStatus.BAD_REQUEST)
        recording = self._managed_recording_path(name)
        dataset, artifact_paths = self._dataset_artifact_paths(recording.name)
        mode = self._recording_storage_mode(recording)
        recording_size = recording.stat().st_size if _is_nonempty_file(recording) else 0
        output_size = sum(_path_tree_size(path) for path in artifact_paths)
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("任务运行期间不能删除录制或重建产物")
            active = (
                self._recording is not None
                and self._recording.absolute() == recording.absolute()
            )
            try:
                if delete_outputs:
                    for path in artifact_paths:
                        if path.is_symlink() or path.is_file():
                            path.unlink(missing_ok=True)
                        elif path.is_dir():
                            shutil.rmtree(path)
                recording.unlink()
            except OSError as exc:
                raise WebActionError(
                    f"删除本地录制失败: {exc}",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            if active:
                self._recording = None
                self._recording_started_at = None
                self._recording_finished_at = None
                if delete_outputs:
                    self._dataset = None
                    self._mesh = None
                    self._conversion_started_at = None
                    self._conversion_finished_at = None
                    self._conversion_progress = None
                elif _is_nonempty_file(self._mesh):
                    self._phase = "completed"
                else:
                    self._phase = "idle"
                if delete_outputs:
                    self._phase = "idle"
            self._error = None
            detail = (
                "并同步删除关联数据集及模型"
                if delete_outputs
                else "，关联产物已保留"
            )
            self._append_log(f"已删除本地录制 {recording.name}{detail}。", level="command")
            self._touch_locked()
            state = self._snapshot_locked()
        return {
            "state": state,
            "recordings": self.recordings_snapshot(),
            "removed": {
                "name": recording.name,
                "recording_bytes": recording_size,
                "output_bytes": output_size if delete_outputs else 0,
                "outputs_deleted": delete_outputs,
                "external_source_preserved": mode in {"hardlink", "symlink"},
            },
        }

    def _finish_conversion_locked(self, returncode: int) -> None:
        self._conversion_finished_at = _now_iso()
        if self._conversion_progress is not None:
            self._conversion_progress["_finished_monotonic"] = time.monotonic()
        cancelled = self._conversion_cancel_requested
        self._reset_conversion_control_locked()
        if cancelled:
            recording_preserved = _is_nonempty_file(self._recording)
            self._phase = "recorded" if recording_preserved else "error"
            self._error = (
                None
                if recording_preserved
                else "重建已终止，但原始录制文件当前不可访问"
            )
            if self._conversion_progress is not None:
                self._conversion_progress.update(
                    label="重建已终止",
                    status="cancelled",
                    paused_at=None,
                    detail=(
                        "重建任务已由用户终止；原始录制文件已保留，可以重新开始"
                        if recording_preserved
                        else self._error
                    ),
                )
                self._conversion_progress.pop("_detail_before_pause", None)
            self._append_log(
                "重建任务已终止，原始录制文件已保留。",
                level="command",
            )
            return

        mesh = self._mesh
        valid = _is_nonempty_file(mesh)
        if returncode == 0 and valid:
            self._phase = "completed"
            self._error = None
            if self._conversion_progress is not None:
                self._conversion_progress.update(
                    stage="complete",
                    stage_index=len(PIPELINE_STEPS),
                    label="重建完成",
                    status="completed",
                    detail="四阶段重建完成，最终网格已生成",
                    processed=self._conversion_progress.get("frame_count"),
                    total=self._conversion_progress.get("frame_count"),
                )
            self._append_log("RGB-D 提取与四阶段重建完成。", level="success")
            self._persist_completed_project_locked()
            return
        self._phase = "error"
        if self._conversion_progress is not None:
            self._conversion_progress["status"] = "failed"
        if returncode == 0:
            self._error = "重建进程已结束，但未找到最终 integrated.ply"
        else:
            self._error = f"重建失败（退出代码 {returncode}），录制文件仍已保留"
        self._append_log(self._error, level="error")

    def start_recording(
        self, *, hardware: object, device: object = 0, name: object = None
    ) -> dict[str, Any]:
        hardware_id = str(hardware or "")
        if hardware_id not in HARDWARE:
            raise WebActionError("请先选择 Azure Kinect、D435 或 D435i", HTTPStatus.BAD_REQUEST)
        try:
            device_index = int(device)
        except (TypeError, ValueError) as exc:
            raise WebActionError("设备编号必须是整数", HTTPStatus.BAD_REQUEST) from exc
        if not 0 <= device_index <= 255:
            raise WebActionError("设备编号必须在 0 到 255 之间", HTTPStatus.BAD_REQUEST)

        spec = HARDWARE[hardware_id]
        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("当前任务尚未结束，不能开始新的录制")
            recording, dataset = self._new_paths_locked(name, spec["extension"])
            self._logs.clear()
            self._phase = "recording"
            self._hardware = hardware_id
            self._device = device_index
            self._recording = recording
            self._dataset = dataset
            self._mesh = dataset / "scene" / "integrated.ply"
            self._loaded_point_cloud = None
            self._project = None
            self._error = None
            self._recording_started_at = _now_iso()
            self._recording_finished_at = None
            self._conversion_started_at = None
            self._conversion_finished_at = None
            self._conversion_progress = None
            self._reconstruction_settings = None
            self._reset_conversion_control_locked()
            live_dir = self._prepare_live_dir_locked()
            self._touch_locked()
            command = [
                *self._launcher_prefix,
                "record",
                "--camera",
                spec["camera"],
                "--sensor",
                str(device_index),
                "--output",
                str(recording),
                "--no-preview",
            ]
            try:
                self._spawn_locked(
                    "record",
                    command,
                    environment_overrides={
                        "OPEN3D_RECONSTRUCT_LIVE_DIR": str(live_dir),
                        "OPEN3D_RECONSTRUCT_LIVE_HARDWARE": hardware_id,
                    },
                )
            except Exception as exc:
                self._phase = "error"
                self._error = str(exc)
                self._discard_live_dir_locked()
                self._append_log(self._error, level="error")
                raise
            self._append_log(
                f"已选择 {spec['label']}（设备 {device_index}），正在录制。",
                level="success",
            )
            return self._snapshot_locked()

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            if os.name == "nt":
                if sig in {signal.SIGINT, signal.SIGTERM}:
                    process.terminate()
                else:
                    process.kill()
            else:
                os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except (OSError, PermissionError) as exc:
            raise WebActionError(f"无法控制任务进程: {exc}") from exc

    def _request_recording_stop_locked(self) -> None:
        if self._live_dir is not None:
            try:
                (self._live_dir / "stop.requested").touch(exist_ok=True)
            except OSError as exc:
                raise WebActionError(f"无法请求录制进程安全停止: {exc}") from exc
        if os.name != "nt" and self._process is not None:
            self._signal_process_group(self._process, signal.SIGINT)

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or self._task != "record":
                raise WebActionError("当前没有正在录制的任务")
            if self._phase == "stopping":
                return self._snapshot_locked()
            self._phase = "stopping"
            self._append_log("正在停止录制并封装文件，请稍候……", level="command")
            self._request_recording_stop_locked()
            generation = self._generation
            threading.Thread(
                target=self._stop_watchdog,
                args=(process, generation),
                name="web-record-stop-watchdog",
                daemon=True,
            ).start()
            self._touch_locked()
            return self._snapshot_locked()

    def _stop_watchdog(self, process: subprocess.Popen[str], generation: int) -> None:
        deadline = time.monotonic() + 30.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if process.poll() is not None:
            return
        with self._lock:
            if self._process is not process or self._generation != generation:
                return
        self._append_log("正常停止等待超时，正在终止卡住的采集进程。", level="error")
        self._signal_process_group(process, signal.SIGTERM)

    @staticmethod
    def _suspend_conversion_process_tree(
        process: subprocess.Popen[str],
    ) -> tuple[psutil.Process, ...]:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGSTOP)
            except ProcessLookupError as exc:
                raise WebActionError("重建进程已经结束，无法暂停") from exc
            except (OSError, PermissionError) as exc:
                raise WebActionError(f"无法暂停重建进程组: {exc}") from exc
            return ()

        try:
            root = psutil.Process(process.pid)
            candidates = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess as exc:
            raise WebActionError("重建进程已经结束，无法暂停") from exc
        except (psutil.AccessDenied, psutil.Error) as exc:
            raise WebActionError(f"无法枚举 Windows 重建进程树: {exc}") from exc

        suspended: list[psutil.Process] = []
        seen: set[int] = set()

        def suspend_one(candidate: psutil.Process) -> None:
            if candidate.pid in seen:
                return
            try:
                candidate.suspend()
            except psutil.NoSuchProcess:
                return
            seen.add(candidate.pid)
            suspended.append(candidate)

        try:
            for candidate in candidates:
                suspend_one(candidate)
            # The root is suspended first. Re-scan to catch a child that was
            # created between the initial process snapshot and that suspend.
            for _attempt in range(3):
                additions = [
                    candidate
                    for candidate in root.children(recursive=True)
                    if candidate.pid not in seen
                ]
                if not additions:
                    break
                for candidate in additions:
                    suspend_one(candidate)
        except (psutil.AccessDenied, psutil.Error) as exc:
            for candidate in reversed(suspended):
                try:
                    candidate.resume()
                except psutil.Error:
                    pass
            raise WebActionError(f"无法暂停完整的 Windows 重建进程树: {exc}") from exc

        if not suspended:
            raise WebActionError("重建进程已经结束，无法暂停")
        return tuple(suspended)

    @staticmethod
    def _resume_conversion_process_tree(
        process: subprocess.Popen[str],
        suspended: tuple[psutil.Process, ...],
    ) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGCONT)
            except ProcessLookupError as exc:
                raise WebActionError("重建进程已经结束，无法继续") from exc
            except (OSError, PermissionError) as exc:
                raise WebActionError(f"无法继续重建进程组: {exc}") from exc
            return

        failures: list[str] = []
        for candidate in reversed(suspended):
            try:
                candidate.resume()
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, psutil.Error) as exc:
                failures.append(f"PID {candidate.pid}: {exc}")
        if failures:
            raise WebActionError(
                "无法继续完整的 Windows 重建进程树: " + "; ".join(failures)
            )

    @staticmethod
    def _terminate_conversion_process_tree(
        process: subprocess.Popen[str], *, force: bool = False
    ) -> None:
        if os.name != "nt":
            ControlCenter._signal_process_group(
                process,
                signal.SIGKILL if force else signal.SIGTERM,
            )
            return

        try:
            root = psutil.Process(process.pid)
            targets = [*reversed(root.children(recursive=True)), root]
        except psutil.NoSuchProcess:
            return
        except (psutil.AccessDenied, psutil.Error) as exc:
            raise WebActionError(f"无法枚举 Windows 重建进程树: {exc}") from exc

        failures: list[str] = []
        for candidate in targets:
            try:
                candidate.kill() if force else candidate.terminate()
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, psutil.Error) as exc:
                failures.append(f"PID {candidate.pid}: {exc}")
        if failures:
            raise WebActionError(
                "无法终止完整的 Windows 重建进程树: " + "; ".join(failures)
            )

    def pause_conversion(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or self._task != "convert":
                raise WebActionError("当前没有正在重建的任务")
            if self._phase == "paused":
                return self._snapshot_locked()
            if self._phase != "converting":
                raise WebActionError("当前重建任务不能暂停")

            suspended = self._suspend_conversion_process_tree(process)
            paused_monotonic = time.monotonic()
            paused_at = _now_iso()
            self._suspended_processes = suspended
            self._conversion_paused_monotonic = paused_monotonic
            self._conversion_paused_at = paused_at
            self._phase = "paused"
            if self._conversion_progress is not None:
                progress = self._conversion_progress
                detail = str(progress.get("detail") or "等待继续重建")
                progress["_detail_before_pause"] = detail
                progress.update(
                    status="paused",
                    paused_at=paused_at,
                    pause_count=int(progress.get("pause_count") or 0) + 1,
                    detail=f"任务已暂停；继续后将从当前位置运行。暂停前：{detail}",
                )
            self._append_log(
                "重建任务已暂停；后台进程与当前进度均已保留。",
                level="command",
            )
            self._touch_locked()
            return self._snapshot_locked()

    def _resume_conversion_locked(self, *, announce: bool) -> None:
        process = self._process
        if process is None or self._task != "convert" or self._phase != "paused":
            raise WebActionError("当前没有已暂停的重建任务")
        paused_monotonic = self._conversion_paused_monotonic
        if paused_monotonic is None:
            raise WebActionError("重建暂停状态不完整，无法安全继续")

        self._resume_conversion_process_tree(process, self._suspended_processes)
        resumed_monotonic = time.monotonic()
        paused_seconds = max(0.0, resumed_monotonic - paused_monotonic)
        progress = self._conversion_progress
        if progress is not None:
            for key in (
                "_started_monotonic",
                "_stage_started_monotonic",
                "_last_output_monotonic",
            ):
                value = progress.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    progress[key] = float(value) + paused_seconds
            detail = progress.pop("_detail_before_pause", None)
            progress.update(
                status="running",
                paused_at=None,
                paused_total_seconds=(
                    float(progress.get("paused_total_seconds") or 0.0)
                    + paused_seconds
                ),
                detail=detail or "重建任务已继续，正在等待新的进度信息",
            )
        self._phase = "converting"
        self._conversion_paused_at = None
        self._conversion_paused_monotonic = None
        self._suspended_processes = ()
        if announce:
            self._append_log(
                f"重建任务已继续；本次暂停 {paused_seconds:.1f} 秒。",
                level="command",
            )
        self._touch_locked()

    def resume_conversion(self) -> dict[str, Any]:
        with self._lock:
            if self._task == "convert" and self._phase == "converting":
                return self._snapshot_locked()
            self._resume_conversion_locked(announce=True)
            return self._snapshot_locked()

    def stop_conversion(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or self._task != "convert":
                raise WebActionError("当前没有正在重建的任务")
            if self._conversion_cancel_requested or self._phase == "cancelling":
                return self._snapshot_locked()

            force = False
            if self._phase == "paused":
                try:
                    self._resume_conversion_locked(announce=False)
                except WebActionError as exc:
                    force = True
                    self._append_log(
                        f"无法先恢复已暂停进程，将强制终止：{exc}",
                        level="error",
                    )
                    self._conversion_paused_at = None
                    self._conversion_paused_monotonic = None
                    self._suspended_processes = ()

            self._conversion_cancel_requested = True
            self._phase = "cancelling"
            if self._conversion_progress is not None:
                self._conversion_progress.update(
                    status="cancelling",
                    paused_at=None,
                    detail="正在终止重建进程；原始 MKV/BAG 录制不会被删除",
                )
            self._append_log(
                "正在终止重建任务；录制文件将完整保留。",
                level="command",
            )
            self._terminate_conversion_process_tree(process, force=force)
            generation = self._generation
            threading.Thread(
                target=self._conversion_stop_watchdog,
                args=(process, generation),
                name="web-convert-stop-watchdog",
                daemon=True,
            ).start()
            self._touch_locked()
            return self._snapshot_locked()

    def _conversion_stop_watchdog(
        self, process: subprocess.Popen[str], generation: int
    ) -> None:
        deadline = time.monotonic() + 8.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is not None:
            return
        with self._lock:
            if self._process is not process or self._generation != generation:
                return
        self._append_log(
            "重建进程未及时退出，正在强制清理整个进程树。",
            level="error",
        )
        self._terminate_conversion_process_tree(process, force=True)

    def start_conversion(
        self,
        *,
        stride: object = 1,
        parameters: object = None,
    ) -> dict[str, Any]:
        try:
            stride_value = int(stride)
        except (TypeError, ValueError) as exc:
            raise WebActionError("帧步长必须是整数", HTTPStatus.BAD_REQUEST) from exc
        if not 1 <= stride_value <= 1000:
            raise WebActionError("帧步长必须在 1 到 1000 之间", HTTPStatus.BAD_REQUEST)
        parameter_values = _validated_reconstruction_parameters(parameters)

        with self._lock:
            if self._process is not None or self._task is not None:
                raise WebActionError("当前任务尚未结束，不能开始重建")
            if not _is_nonempty_file(self._recording):
                raise WebActionError("没有可重建的录制文件；请先完成录制")
            assert self._recording is not None
            assert self._dataset is not None
            command = [
                *self._launcher_prefix,
                "reconstruct",
                str(self._recording),
                "--dataset",
                str(self._dataset),
                "--stride",
                str(stride_value),
            ]
            for key, value in parameter_values.items():
                encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
                command.extend(("--set", f"{key}={encoded}"))
            if self._dataset.exists():
                command.append("--force-extract")
            self._phase = "converting"
            self._error = None
            self._conversion_started_at = _now_iso()
            self._conversion_finished_at = None
            self._mesh = self._dataset / "scene" / "integrated.ply"
            self._loaded_point_cloud = None
            self._project = None
            self._reconstruction_settings = {
                "stride": stride_value,
                **parameter_values,
            }
            self._reset_conversion_control_locked()
            self._reset_conversion_progress_locked()
            self._append_log(
                "开始提取 RGB-D 帧并执行 make、register、refine、integrate 四阶段重建。",
                level="command",
            )
            if parameter_values:
                readable = "，".join(
                    f"{key}={value}" for key, value in self._reconstruction_settings.items()
                )
                self._append_log(f"本次重建参数：{readable}", level="command")
            self._touch_locked()
            try:
                self._spawn_locked(
                    "convert",
                    command,
                    environment_overrides={
                        "OPEN3D_RECONSTRUCT_WEB_METRICS": "1",
                    },
                )
            except Exception as exc:
                self._phase = "error"
                self._error = str(exc)
                self._append_log(self._error, level="error")
                raise
            return self._snapshot_locked()

    @staticmethod
    def _versioned_file(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if not path.is_file() or stat.st_size <= 0:
            return None
        return {
            "path": _display_path(path),
            "size": stat.st_size,
            "version": f"{stat.st_mtime_ns}-{stat.st_size}",
        }

    @staticmethod
    def _latest_file(directory: Path, patterns: tuple[str, ...]) -> Path | None:
        if not directory.is_dir():
            return None
        candidates: list[Path] = []
        for pattern in patterns:
            candidates.extend(directory.glob(pattern))
        if not candidates:
            return None
        latest = max(candidates, key=lambda path: path.name)
        return latest if _is_nonempty_file(latest) else None

    @staticmethod
    def _ordered_files(directory: Path, patterns: tuple[str, ...]) -> list[Path]:
        if not directory.is_dir():
            return []
        candidates: set[Path] = set()
        for pattern in patterns:
            candidates.update(directory.glob(pattern))
        return sorted(
            (path for path in candidates if _is_nonempty_file(path)),
            key=lambda path: path.name,
        )

    def _live_snapshot_locked(self) -> dict[str, Any] | None:
        directory = self._live_dir
        if directory is None:
            return None
        state_path = directory / "state.json"
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            value = {
                "active": self._phase in {"recording", "stopping"},
                "hardware": self._hardware,
                "sequence": 0,
                "frame_count": 0,
                "fps": 0.0,
                "updated_at": None,
                "rgb": None,
                "depth": None,
                "imu": None,
                "error": None,
            }
        if not isinstance(value, dict):
            return None
        rgb = self._versioned_file(directory / "rgb.jpg")
        depth = self._versioned_file(directory / "depth.jpg")
        value["rgb_url"] = "/api/live/rgb" if rgb else None
        value["depth_url"] = "/api/live/depth" if depth else None
        value["rgb_file"] = rgb
        value["depth_file"] = depth
        return value

    def _process_paths_locked(self, frame_index: int | None = None) -> dict[str, Any]:
        dataset = self._dataset
        if dataset is None:
            return {
                "rgb": None,
                "depth": None,
                "model": None,
                "frame_index": None,
                "frame_count": 0,
                "working": None,
            }
        extracting = dataset.with_name(dataset.name + ".extracting")
        working = dataset if dataset.is_dir() else extracting
        rgb_files = self._ordered_files(working / "color", ("*.jpg", "*.png"))
        depth_files = self._ordered_files(working / "depth", ("*.png",))
        frame_count = min(len(rgb_files), len(depth_files))
        selected_index: int | None = None
        rgb: Path | None = None
        depth: Path | None = None
        if frame_count:
            selected_index = frame_count - 1 if frame_index is None else max(
                0, min(int(frame_index), frame_count - 1)
            )
            rgb = rgb_files[selected_index]
            depth = depth_files[selected_index]
        model: Path | None = None
        if _is_nonempty_file(self._mesh):
            model = self._mesh
        else:
            model = self._latest_file(
                working / "fragments",
                ("fragment_optimized_*.ply", "fragment_*.ply"),
            )
        return {
            "rgb": rgb,
            "depth": depth,
            "model": model,
            "frame_index": selected_index,
            "frame_count": frame_count,
            "working": working,
        }

    def _process_snapshot_locked(self) -> dict[str, Any] | None:
        if self._conversion_progress is None:
            return None
        progress = dict(self._conversion_progress)
        stage = progress.get("stage")
        clock_now = time.monotonic()
        finished_monotonic = progress.pop("_finished_monotonic", None)
        now = self._conversion_paused_monotonic or finished_monotonic or clock_now
        started = float(progress.pop("_started_monotonic", now))
        stage_started = float(progress.pop("_stage_started_monotonic", started))
        last_output = float(progress.pop("_last_output_monotonic", started))
        progress.pop("_detail_before_pause", None)
        elapsed = max(0.0, now - started)
        stage_elapsed = max(0.0, now - stage_started)
        quiet = max(0.0, now - last_output)
        paused_total = float(progress.get("paused_total_seconds") or 0.0)
        if self._conversion_paused_monotonic is not None:
            paused_total += max(
                0.0, clock_now - self._conversion_paused_monotonic
            )
        progress.update(
            elapsed_seconds=elapsed,
            stage_elapsed_seconds=stage_elapsed,
            quiet_seconds=quiet,
            paused_total_seconds=paused_total,
            process_alive=self._process is not None and self._process.poll() is None,
            paused=self._phase == "paused",
            paused_at=self._conversion_paused_at,
            heartbeat_at=_now_iso(),
        )

        events = [dict(event) for event in progress.pop("_matching_events", [])]
        active = [
            dict(event)
            for event in progress.pop("_matching_active", {}).values()
        ]
        progress.pop("_matching_seen", None)
        attempted = int(progress.pop("_matching_attempted", 0) or 0)
        succeeded = int(progress.pop("_matching_succeeded", 0) or 0)
        failed = int(progress.pop("_matching_failed", 0) or 0)
        information_max = float(
            progress.pop("_matching_information_max", 0.0) or 0.0
        )
        last_match = progress.pop("_matching_last", None)
        expected_matches = progress.pop("_matching_expected", None)
        settings = progress.get("settings") or {}
        progress["matching"] = {
            "frame_count": int(progress.get("frame_count") or 0),
            "frames_per_fragment": int(
                settings.get("n_frames_per_fragment", 100)
            ),
            "expected": expected_matches,
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "information_max": information_max,
            "active": active,
            "events": events,
            "last": dict(last_match) if isinstance(last_match, dict) else None,
        }

        paths = self._process_paths_locked()

        if stage == "make" and isinstance(paths.get("working"), Path):
            fragment_files = [
                path
                for path in self._ordered_files(
                    paths["working"] / "fragments", ("fragment_*.ply",)
                )
                if re.fullmatch(r"fragment_\d+\.ply", path.name)
            ]
            completed_fragments = max(
                len(fragment_files), int(progress.get("fragment_completed") or 0)
            )
            fragment_total = progress.get("fragment_total")
            if isinstance(fragment_total, int) and fragment_total > 0:
                progress["fragment_completed"] = min(completed_fragments, fragment_total)
                progress["processed"] = min(completed_fragments, fragment_total)
                progress["total"] = fragment_total

        rgb = self._versioned_file(paths["rgb"])
        depth = self._versioned_file(paths["depth"])
        model = self._versioned_file(paths["model"])
        rgbd_artifact: dict[str, Any] | None = None
        model_artifact: dict[str, Any] | None = None
        if rgb and depth:
            frame_index = int(paths["frame_index"] or 0)
            frame_count = int(paths["frame_count"] or 0)
            rgbd_artifact = {
                "kind": "rgbd",
                "rgb": rgb,
                "depth": depth,
                "rgb_url": f"/api/process/rgb?frame={frame_index}",
                "depth_url": f"/api/process/depth?frame={frame_index}",
                "version": f"{frame_index}-{rgb['version']}-{depth['version']}",
                "frame_index": frame_index,
                "frame_count": frame_count,
                "label": (
                    "输入序列预览" if stage == "make" else "最新提取帧"
                ),
            }
        if model:
            model_artifact = {
                "kind": "model",
                "model": model,
                "model_url": "/api/process/model",
                "version": model["version"],
                "label": (
                    "最终融合网格" if paths["model"] == self._mesh else "最新局部片段"
                ),
            }
        progress["artifacts"] = {"rgbd": rgbd_artifact, "model": model_artifact}
        progress["artifact"] = (
            rgbd_artifact if stage == "extract" else (model_artifact or rgbd_artifact)
        )
        return progress

    def _snapshot_locked(self) -> dict[str, Any]:
        process = self._process
        busy = process is not None or self._task is not None
        recording = _file_summary(self._recording)
        mesh = _file_summary(self._mesh)
        loaded_point_cloud = _file_summary(self._loaded_point_cloud)
        recording_ready = bool(recording and recording["exists"] and recording["size"] > 0)
        mesh_ready = bool(mesh and mesh["exists"] and mesh["size"] > 0)
        loaded_point_cloud_ready = bool(
            loaded_point_cloud
            and loaded_point_cloud["exists"]
            and loaded_point_cloud["size"] > 0
        )
        import_progress: dict[str, Any] | None = None
        if self._task == "import":
            total_bytes = max(0, self._import_expected_bytes)
            received_bytes = min(
                max(0, self._import_received_bytes), total_bytes
            )
            import_progress = {
                "name": self._import_target.name if self._import_target else None,
                "received_bytes": received_bytes,
                "total_bytes": total_bytes,
                "percent": (
                    received_bytes / total_bytes * 100.0 if total_bytes else 0.0
                ),
            }
        return {
            "phase": self._phase,
            "revision": self._revision,
            "hardware": self._hardware,
            "hardware_label": HARDWARE.get(self._hardware or "", {}).get("label"),
            "device": self._device,
            "task": self._task,
            "pid": process.pid if process is not None else None,
            "error": self._error,
            "recording": recording,
            "dataset": _display_path(self._dataset),
            "mesh": mesh,
            "loaded_point_cloud": loaded_point_cloud,
            "project": dict(self._project) if self._project is not None else None,
            "recording_started_at": self._recording_started_at,
            "recording_finished_at": self._recording_finished_at,
            "conversion_started_at": self._conversion_started_at,
            "conversion_finished_at": self._conversion_finished_at,
            "conversion_paused_at": self._conversion_paused_at,
            "reconstruction_settings": self._reconstruction_settings,
            "recording_import": import_progress,
            "can_start_recording": not busy,
            "can_import_recording": not busy,
            "can_open_project": not busy,
            "can_stop_recording": process is not None and self._task == "record" and self._phase == "recording",
            "can_start_conversion": not busy and recording_ready,
            "can_pause_conversion": process is not None and self._task == "convert" and self._phase == "converting",
            "can_resume_conversion": process is not None and self._task == "convert" and self._phase == "paused",
            "can_stop_conversion": process is not None and self._task == "convert" and self._phase in {"converting", "paused"},
            "can_select_point_cloud": not busy,
            "recording_url": "/api/files/recording" if recording_ready else None,
            "mesh_url": "/api/files/mesh" if mesh_ready else None,
            "mesh_preview_url": "/api/files/mesh-preview" if mesh_ready else None,
            "project_url": (
                "/api/files/project" if self._project is not None else None
            ),
            "loaded_point_cloud_url": (
                "/api/files/point-cloud" if loaded_point_cloud_ready else None
            ),
            "live": self._live_snapshot_locked(),
            "conversion": self._process_snapshot_locked(),
            "logs": list(self._logs),
            "server_time": _now_iso(),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def live_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return self._live_snapshot_locked()

    def live_file(self, kind: str) -> Path | None:
        if kind not in {"rgb", "depth"}:
            return None
        with self._lock:
            directory = self._live_dir
            if directory is None:
                return None
            path = directory / f"{kind}.jpg"
            if not _is_nonempty_file(path):
                return None
            try:
                path.resolve().relative_to(directory.resolve())
            except ValueError:
                return None
            return path.resolve()

    def process_file(self, kind: str, *, frame_index: int | None = None) -> Path | None:
        if kind not in {"rgb", "depth", "model"}:
            return None
        with self._lock:
            path = self._process_paths_locked(frame_index).get(kind)
            dataset = self._dataset
            if path is None or dataset is None or not _is_nonempty_file(path):
                return None
            extracting = dataset.with_name(dataset.name + ".extracting")
            try:
                if dataset.is_dir():
                    path.resolve().relative_to(dataset.resolve())
                else:
                    path.resolve().relative_to(extracting.resolve())
            except ValueError:
                return None
            return path.resolve()

    def result_file(self, kind: str) -> Path | None:
        with self._lock:
            if kind == "mesh":
                path = self._mesh
            elif kind == "recording":
                path = self._recording
            elif kind == "point-cloud":
                path = self._loaded_point_cloud
            elif kind == "project":
                path = self._dataset / PROJECT_FILENAME if self._dataset else None
            else:
                return None
            if not _is_nonempty_file(path):
                return None
            assert path is not None
            try:
                if kind in {"mesh", "project"}:
                    base = (
                        self._dataset.resolve()
                        if self._dataset is not None
                        else ROOT.resolve()
                    )
                    path.resolve().relative_to(base)
                elif kind == "recording":
                    if self._project is None:
                        path.absolute().relative_to(RECORDINGS_DIR.resolve())
                    elif path.suffix.lower() not in {".mkv", ".bag"}:
                        return None
                elif path.suffix.lower() not in POINT_CLOUD_SUFFIXES:
                    return None
            except ValueError:
                return None
            return path.absolute() if kind == "recording" else path.resolve()

    def mesh_preview_file(self) -> Path | None:
        """Return a cached browser-sized triangle mesh without changing the result."""
        source = self.result_file("mesh")
        if source is None:
            return None
        preview = source.with_name(
            f"{source.stem}.preview-v{MESH_PREVIEW_CACHE_VERSION}.ply"
        )

        def cache_is_current() -> bool:
            try:
                return (
                    preview.is_file()
                    and preview.stat().st_size > 0
                    and preview.stat().st_mtime_ns >= source.stat().st_mtime_ns
                )
            except OSError:
                return False

        if cache_is_current():
            return preview.resolve()

        with self._mesh_preview_lock:
            if cache_is_current():
                return preview.resolve()
            self._append_log(
                "正在生成浏览器网格预览；完整 PLY 不会被修改。",
                level="command",
            )
            started = time.monotonic()
            temporary: Path | None = None
            try:
                import open3d as o3d

                mesh = o3d.io.read_triangle_mesh(
                    str(source),
                    enable_post_processing=False,
                    print_progress=False,
                )
                source_triangles = len(mesh.triangles)
                if source_triangles <= 0:
                    raise WebActionError(
                        "重建结果只有点数据，没有可供网格预览的三角面",
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    )

                preview_mesh = mesh
                if source_triangles > MESH_PREVIEW_TARGET_TRIANGLES:
                    preview_mesh = mesh.simplify_quadric_decimation(
                        target_number_of_triangles=MESH_PREVIEW_TARGET_TRIANGLES,
                    )
                preview_mesh.remove_degenerate_triangles()
                preview_mesh.remove_duplicated_triangles()
                preview_mesh.remove_unreferenced_vertices()
                preview_mesh.compute_vertex_normals()

                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{source.stem}-preview-",
                    suffix=".ply",
                    dir=source.parent,
                )
                os.close(descriptor)
                temporary = Path(temporary_name)
                written = o3d.io.write_triangle_mesh(
                    str(temporary),
                    preview_mesh,
                    write_ascii=False,
                    compressed=False,
                    write_vertex_normals=True,
                    write_vertex_colors=True,
                    write_triangle_uvs=False,
                    print_progress=False,
                )
                if not written or not _is_nonempty_file(temporary):
                    raise WebActionError(
                        "网格预览文件写入失败",
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                os.replace(temporary, preview)
                temporary = None
                elapsed = time.monotonic() - started
                self._append_log(
                    "浏览器网格预览已就绪："
                    f"{source_triangles:,} 面 → {len(preview_mesh.triangles):,} 面，"
                    f"用时 {elapsed:.1f}s。",
                    level="success",
                )
                return preview.resolve()
            except WebActionError:
                raise
            except Exception as exc:
                message = f"生成浏览器网格预览失败: {exc}"
                self._append_log(message, level="error")
                raise WebActionError(
                    message,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def discover_devices(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._device_lock:
            with self._lock:
                active = self._process is not None or self._task is not None
                if self._device_cache is not None and (
                    active or (not refresh and time.monotonic() - self._device_cache_at < 10.0)
                ):
                    return self._device_cache

            items: dict[str, dict[str, Any]] = {
                hardware_id: {
                    "id": hardware_id,
                    "label": spec["label"],
                    "available": False,
                    "devices": [],
                    "error": None,
                    "configuration": _camera_configuration(hardware_id),
                }
                for hardware_id, spec in HARDWARE.items()
            }

            if active:
                for item in items.values():
                    item["error"] = "任务运行期间暂停设备枚举"
                return {"items": list(items.values()), "detected_at": _now_iso()}

            try:
                from .doctor import k4a_device_count

                count = k4a_device_count()
                items["azure-kinect"]["devices"] = [
                    {"index": index, "name": "Azure Kinect DK", "serial": None}
                    for index in range(count)
                ]
                items["azure-kinect"]["available"] = count > 0
            except Exception as exc:
                items["azure-kinect"]["error"] = str(exc)

            try:
                from .realsense import device_model, enumerate_devices, is_supported_device_name

                for index, device in enumerate(enumerate_devices()):
                    name = str(getattr(device, "name", "RealSense"))
                    if not is_supported_device_name(name):
                        continue
                    hardware_id = "d435i" if device_model(name) == "D435i" else "d435"
                    device_info = {
                        "index": index,
                        "name": name,
                        "serial": str(getattr(device, "serial", "")) or None,
                    }
                    for output_key, attribute in (
                        ("firmware", "firmware_version"),
                        ("usb_type", "usb_type_descriptor"),
                        ("product_line", "product_line"),
                    ):
                        try:
                            attribute_value = getattr(device, attribute, None)
                        except Exception:
                            attribute_value = None
                        if attribute_value is not None and str(attribute_value):
                            device_info[output_key] = str(attribute_value)
                    items[hardware_id]["devices"].append(device_info)
                    items[hardware_id]["available"] = True
            except Exception as exc:
                message = str(exc)
                items["d435"]["error"] = message
                items["d435i"]["error"] = message

            result = {"items": list(items.values()), "detected_at": _now_iso()}
            with self._lock:
                self._device_cache = result
                self._device_cache_at = time.monotonic()
            return result

    def close(self) -> None:
        with self._lock:
            process = self._process
            import_token = self._generation if self._task == "import" else None
        if import_token is not None:
            self.abort_recording_import(
                import_token,
                "Web 服务退出，已有录制文件导入已中止。",
            )
        try:
            if process is None or process.poll() is not None:
                return
            self._append_log("Web 服务正在退出，先停止当前子任务。", level="command")
            force_conversion_stop = False
            with self._lock:
                recording = self._task == "record"
                converting = self._task == "convert"
                if recording:
                    self._request_recording_stop_locked()
                elif converting:
                    self._conversion_cancel_requested = True
                    if self._phase == "paused":
                        try:
                            self._resume_conversion_locked(announce=False)
                        except WebActionError:
                            force_conversion_stop = True
                            self._conversion_paused_at = None
                            self._conversion_paused_monotonic = None
                            self._suspended_processes = ()
            if converting:
                self._terminate_conversion_process_tree(
                    process, force=force_conversion_stop
                )
            elif not recording:
                self._signal_process_group(process, signal.SIGINT)
            try:
                process.wait(timeout=12)
                return
            except subprocess.TimeoutExpired:
                pass
            if converting:
                self._terminate_conversion_process_tree(process, force=True)
            else:
                self._signal_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                if converting:
                    self._terminate_conversion_process_tree(process, force=True)
                else:
                    self._signal_process_group(process, signal.SIGKILL)
        finally:
            with self._lock:
                self._discard_live_dir_locked()


class LocalWebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _handler_class(controller: ControlCenter, instance_id: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "open3d-reconstruct-web/0.4"
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            return

        def handle(self) -> None:
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                # Browsers legitimately cancel superseded image requests.
                return

        def _host_is_local(self) -> bool:
            host = self.headers.get("Host", "").lower()
            return host in {"127.0.0.1", "localhost"} or host.startswith(
                ("127.0.0.1:", "localhost:")
            )

        def _headers(self, content_type: str, length: int, *, cache: str = "no-store") -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )

        def _send_bytes(
            self,
            payload: bytes,
            *,
            status: int = HTTPStatus.OK,
            content_type: str = "application/octet-stream",
            cache: str = "no-store",
        ) -> None:
            self.send_response(int(status))
            self._headers(content_type, len(payload), cache=cache)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, value: object, *, status: int = HTTPStatus.OK) -> None:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send_bytes(
                payload,
                status=status,
                content_type="application/json; charset=utf-8",
            )

        def _error(self, status: int, message: str) -> None:
            self.close_connection = True
            self._send_json({"ok": False, "error": message}, status=status)

        def _read_json(self) -> dict[str, Any]:
            if self.headers.get("X-Open3D-Reconstruct") != "web":
                raise WebActionError("请求来源校验失败", HTTPStatus.FORBIDDEN)
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise WebActionError("请求必须使用 application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise WebActionError("Content-Length 无效", HTTPStatus.BAD_REQUEST) from exc
            if not 0 <= length <= MAX_REQUEST_BYTES:
                raise WebActionError("请求内容过大", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise WebActionError("JSON 请求格式无效", HTTPStatus.BAD_REQUEST) from exc
            if not isinstance(value, dict):
                raise WebActionError("JSON 请求必须是对象", HTTPStatus.BAD_REQUEST)
            return value

        def _receive_recording_import(self, parsed) -> dict[str, Any]:
            if self.headers.get("X-Open3D-Reconstruct") != "web":
                raise WebActionError("请求来源校验失败", HTTPStatus.FORBIDDEN)
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/octet-stream":
                raise WebActionError(
                    "录制文件必须使用 application/octet-stream 上传",
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
            if self.headers.get("Transfer-Encoding"):
                raise WebActionError(
                    "录制文件导入不支持分块传输",
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise WebActionError(
                    "录制文件 Content-Length 无效",
                    HTTPStatus.BAD_REQUEST,
                ) from exc
            if length <= 0:
                raise WebActionError("录制文件为空", HTTPStatus.BAD_REQUEST)
            if length > MAX_RECORDING_UPLOAD_BYTES:
                raise WebActionError(
                    "录制文件超过 256 GiB 导入上限",
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            free_bytes = shutil.disk_usage(RECORDINGS_DIR).free
            if length + MIN_FREE_STORAGE_BYTES > free_bytes:
                raise WebActionError(
                    "项目磁盘剩余空间不足，无法导入该录制文件",
                    HTTPStatus.INSUFFICIENT_STORAGE,
                )

            query = parse_qs(parsed.query)
            filename = query.get("filename", [None])[0]
            hardware = query.get("hardware", [None])[0]
            token, temporary = controller.begin_recording_import(
                filename=filename,
                hardware=hardware,
                size=length,
            )
            received = 0
            try:
                with temporary.open("wb") as destination:
                    while received < length:
                        chunk = self.rfile.read(
                            min(RECORDING_UPLOAD_CHUNK_BYTES, length - received)
                        )
                        if not chunk:
                            raise WebActionError(
                                f"录制文件传输中断：{received}/{length} 字节",
                                HTTPStatus.BAD_REQUEST,
                            )
                        destination.write(chunk)
                        received += len(chunk)
                        controller.update_recording_import(token, received)
                    destination.flush()
                    os.fsync(destination.fileno())
                return controller.finish_recording_import(token)
            except WebActionError as exc:
                controller.abort_recording_import(token, str(exc))
                raise
            except (OSError, ConnectionError) as exc:
                message = f"录制文件导入失败: {exc}"
                controller.abort_recording_import(token, message)
                raise WebActionError(
                    message,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

        def _send_static(self, filename: str, content_type: str) -> None:
            path = WEBUI_DIR / filename
            try:
                payload = path.read_bytes()
            except OSError:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"页面资源缺失: {filename}")
                return
            self._send_bytes(
                payload,
                content_type=content_type,
                cache="no-cache",
            )

        def _send_file(self, kind: str) -> None:
            try:
                path = (
                    controller.mesh_preview_file()
                    if kind == "mesh-preview"
                    else controller.result_file(kind)
                )
            except WebActionError as exc:
                self._error(exc.status, str(exc))
                return
            if path is None:
                self._error(HTTPStatus.NOT_FOUND, "文件尚未生成或已不存在")
                return
            inline_kinds = {"mesh", "mesh-preview", "point-cloud"}
            content_type = (
                "model/ply"
                if kind in inline_kinds
                else ("application/json; charset=utf-8" if kind == "project" else "application/octet-stream")
            )
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self._headers(content_type, size, cache="no-store")
            disposition = "inline" if kind in inline_kinds else "attachment"
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{quote(path.name)}",
            )
            self.end_headers()
            try:
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _stream_visual_file(self, path: Path, content_type: str) -> None:
            try:
                source = path.open("rb")
            except OSError:
                self._error(HTTPStatus.NOT_FOUND, "可视化内容已更新，请重试")
                return
            with source:
                size = os.fstat(source.fileno()).st_size
                self.send_response(HTTPStatus.OK)
                self._headers(content_type, size, cache="no-store")
                self.end_headers()
                try:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _send_visual(
            self,
            scope: str,
            kind: str,
            *,
            frame_index: int | None = None,
        ) -> None:
            path = (
                controller.live_file(kind)
                if scope == "live"
                else controller.process_file(kind, frame_index=frame_index)
            )
            if path is None:
                self._error(HTTPStatus.NOT_FOUND, "可视化内容尚未生成")
                return
            if scope == "process" and kind == "depth":
                try:
                    import cv2

                    from .live import LivePreviewPublisher

                    depth = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                    if depth is None:
                        raise RuntimeError("深度图无法读取")
                    colored, _valid, _values = LivePreviewPublisher._depth_preview(
                        depth
                    )
                    ok, encoded = cv2.imencode(
                        ".jpg", colored, [cv2.IMWRITE_JPEG_QUALITY, 88]
                    )
                    if not ok:
                        raise RuntimeError("深度伪彩编码失败")
                    self._send_bytes(encoded.tobytes(), content_type="image/jpeg")
                except Exception as exc:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"无法生成深度预览: {exc}",
                    )
                return
            if kind == "model":
                content_type = "model/ply"
            elif path.suffix.lower() == ".png":
                content_type = "image/png"
            else:
                content_type = "image/jpeg"
            self._stream_visual_file(path, content_type)

        def do_GET(self) -> None:
            if not self._host_is_local():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "仅接受本机访问")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._send_static("index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.css":
                self._send_static("app.css", "text/css; charset=utf-8")
            elif parsed.path == "/app.js":
                self._send_static("app.js", "text/javascript; charset=utf-8")
            elif parsed.path == "/api/state":
                self._send_json({"ok": True, "state": controller.snapshot()})
            elif parsed.path == "/api/health":
                state = controller.snapshot()
                self._send_json(
                    {
                        "ok": True,
                        "service": SERVICE_NAME,
                        "version": __version__,
                        "pid": os.getpid(),
                        "instance_id": instance_id,
                        "port": int(self.server.server_address[1]),
                        "phase": state["phase"],
                        "task": state["task"],
                    }
                )
            elif parsed.path == "/api/devices":
                query = parse_qs(parsed.query)
                self._send_json(
                    {
                        "ok": True,
                        "devices": controller.discover_devices(
                            refresh=query.get("refresh") == ["1"]
                        ),
                    }
                )
            elif parsed.path == "/api/recordings":
                self._send_json(
                    {"ok": True, "recordings": controller.recordings_snapshot()}
                )
            elif parsed.path == "/api/reconstruction/config":
                try:
                    configuration = _reconstruction_configuration()
                except ValueError as exc:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"重建 YAML 无效: {exc}",
                    )
                    return
                self._send_json({"ok": True, "configuration": configuration})
            elif parsed.path == "/api/live/state":
                self._send_json({"ok": True, "live": controller.live_snapshot()})
            elif parsed.path in {"/api/live/rgb", "/api/live/depth"}:
                self._send_visual("live", parsed.path.rsplit("/", 1)[-1])
            elif parsed.path in {
                "/api/process/rgb",
                "/api/process/depth",
                "/api/process/model",
            }:
                frame_index: int | None = None
                raw_frame = parse_qs(parsed.query).get("frame", [None])[0]
                if raw_frame is not None:
                    try:
                        frame_index = int(raw_frame)
                    except (TypeError, ValueError):
                        self._error(HTTPStatus.BAD_REQUEST, "预览帧编号无效")
                        return
                self._send_visual(
                    "process",
                    parsed.path.rsplit("/", 1)[-1],
                    frame_index=frame_index,
                )
            elif parsed.path == "/api/files/recording":
                self._send_file("recording")
            elif parsed.path == "/api/files/mesh":
                self._send_file("mesh")
            elif parsed.path == "/api/files/mesh-preview":
                self._send_file("mesh-preview")
            elif parsed.path == "/api/files/point-cloud":
                self._send_file("point-cloud")
            elif parsed.path == "/api/files/project":
                self._send_file("project")
            else:
                self._error(HTTPStatus.NOT_FOUND, "页面不存在")

        def do_POST(self) -> None:
            if not self._host_is_local():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "仅接受本机访问")
                return
            parsed = urlsplit(self.path)
            try:
                if parsed.path == "/api/service/shutdown":
                    token = self.headers.get("X-Open3D-Reconstruct-Service", "")
                    if not instance_id or token != instance_id:
                        self._error(HTTPStatus.FORBIDDEN, "服务停止令牌无效")
                        return
                    self._read_json()
                    self._send_json({"ok": True, "shutting_down": True})
                    threading.Thread(
                        target=self.server.shutdown,
                        name="web-service-shutdown",
                        daemon=True,
                    ).start()
                    return
                if parsed.path == "/api/recording/import":
                    state = self._receive_recording_import(parsed)
                    self._send_json({"ok": True, "state": state})
                    return
                body = self._read_json()
                if parsed.path == "/api/recording/select-local":
                    selected = controller.choose_local_recording(
                        hardware=body.get("hardware")
                    )
                    self._send_json(
                        {
                            "ok": True,
                            "cancelled": selected is None,
                            "state": selected or controller.snapshot(),
                        }
                    )
                    return
                if parsed.path == "/api/project/select-local":
                    selected = controller.choose_local_project()
                    self._send_json(
                        {
                            "ok": True,
                            "cancelled": selected is None,
                            "state": selected or controller.snapshot(),
                        }
                    )
                    return
                if parsed.path == "/api/point-cloud/select-local":
                    selected = controller.choose_local_point_cloud()
                    self._send_json(
                        {
                            "ok": True,
                            "cancelled": selected is None,
                            "state": selected or controller.snapshot(),
                        }
                    )
                    return
                if parsed.path == "/api/point-cloud/clear":
                    self._send_json(
                        {"ok": True, "state": controller.clear_local_point_cloud()}
                    )
                    return
                if parsed.path == "/api/recordings/use":
                    state = controller.select_managed_recording(
                        body.get("name"), hardware=body.get("hardware")
                    )
                elif parsed.path == "/api/recordings/delete":
                    result = controller.delete_managed_recording(
                        body.get("name"),
                        delete_outputs=body.get("delete_outputs", False),
                    )
                    self._send_json({"ok": True, **result})
                    return
                elif parsed.path == "/api/record/start":
                    state = controller.start_recording(
                        hardware=body.get("hardware"),
                        device=body.get("device", 0),
                        name=body.get("name"),
                    )
                elif parsed.path == "/api/record/stop":
                    state = controller.stop_recording()
                elif parsed.path == "/api/convert/start":
                    state = controller.start_conversion(
                        stride=body.get("stride", 1),
                        parameters=body.get("parameters"),
                    )
                elif parsed.path == "/api/convert/pause":
                    state = controller.pause_conversion()
                elif parsed.path == "/api/convert/resume":
                    state = controller.resume_conversion()
                elif parsed.path == "/api/convert/stop":
                    state = controller.stop_conversion()
                else:
                    self._error(HTTPStatus.NOT_FOUND, "接口不存在")
                    return
            except WebActionError as exc:
                self._error(exc.status, str(exc))
                return
            except Exception as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"内部错误: {exc}")
                return
            self._send_json({"ok": True, "state": state})

        def do_OPTIONS(self) -> None:
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "不允许跨来源请求")

    return Handler


def create_server(
    controller: ControlCenter | None = None,
    *,
    host: str = WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    instance_id: str | None = None,
) -> LocalWebServer:
    if host != WEB_HOST:
        raise ValueError(f"Web 服务只允许监听本机地址 {WEB_HOST}")
    if not 1 <= port <= 65535 and port != 0:
        raise ValueError("端口必须在 1 到 65535 之间")
    control = controller or ControlCenter()
    try:
        return LocalWebServer((host, port), _handler_class(control, instance_id))
    except OSError as exc:
        raise RuntimeError(f"无法监听 http://{host}:{port}：{exc}") from exc


def serve_web(*, port: int = DEFAULT_WEB_PORT, open_browser: bool = True) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    ensure_local_directories()
    # Fail before daemonizing if a user edit made the shared Web/YAML presets
    # invalid.  This validation does not probe Torch and therefore does not add
    # GPU import latency to the service health-check window.
    reconstruction_profile_catalog(DEFAULT_RECONSTRUCTION_PROFILES)
    with SingletonLease(port=port) as lease:
        assert lease.metadata is not None
        controller = ControlCenter()
        server = create_server(
            controller,
            port=port,
            instance_id=lease.metadata.instance_id,
        )
        actual_port = int(server.server_address[1])
        url = f"http://{WEB_HOST}:{actual_port}/"
        print(f"Web 控制台已启动：{url}")
        print("相机、API、日志与模型文件均使用同一个本地端口；按 Ctrl+C 退出。")

        if open_browser:
            def open_page() -> None:
                time.sleep(0.35)
                webbrowser.open(url)

            threading.Thread(
                target=open_page,
                name="web-open-browser",
                daemon=True,
            ).start()

        handles_signals = threading.current_thread() is threading.main_thread()
        previous_sigterm = signal.getsignal(signal.SIGTERM) if handles_signals else None

        def stop_for_sigterm(_signum, _frame) -> None:
            raise KeyboardInterrupt

        if handles_signals:
            signal.signal(signal.SIGTERM, stop_for_sigterm)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\n正在关闭 Web 控制台……")
        finally:
            if handles_signals:
                signal.signal(signal.SIGTERM, previous_sigterm)
            server.server_close()
            controller.close()
    return 0
