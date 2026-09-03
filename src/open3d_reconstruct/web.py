from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
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

from . import __version__
from .paths import (
    DATASETS_DIR,
    RECORDINGS_DIR,
    ROOT,
    RUNTIME_DIR,
    ensure_local_directories,
)
from .service import DEFAULT_SERVICE_PORT, SERVICE_NAME, SingletonLease


DEFAULT_WEB_PORT = DEFAULT_SERVICE_PORT
WEB_HOST = "127.0.0.1"
WEBUI_DIR = Path(__file__).resolve().parent / "webui"
MAX_REQUEST_BYTES = 32 * 1024
MAX_LOG_LINES = 1200
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


class WebActionError(RuntimeError):
    def __init__(self, message: str, status: int = HTTPStatus.CONFLICT) -> None:
        super().__init__(message)
        self.status = int(status)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


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


class ControlCenter:
    """Owns the single local camera/conversion process used by the web page."""

    def __init__(self, launcher: Path | None = None) -> None:
        self.launcher = (launcher or (ROOT / "open3d-reconstruct")).resolve()
        self._lock = threading.RLock()
        self._device_lock = threading.Lock()
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
        self._error: str | None = None
        self._recording_started_at: str | None = None
        self._recording_finished_at: str | None = None
        self._conversion_started_at: str | None = None
        self._conversion_finished_at: str | None = None
        self._conversion_progress: dict[str, Any] | None = None
        self._live_dir: Path | None = None
        self._device_cache: dict[str, Any] | None = None
        self._device_cache_at = 0.0

    def _touch_locked(self) -> None:
        self._revision += 1

    def _reset_conversion_progress_locked(self) -> None:
        self._conversion_progress = {
            "stage": "extract",
            "stage_index": 0,
            "stage_count": len(PIPELINE_STEPS),
            "label": PIPELINE_STEPS[0][1],
            "status": "running",
            "detail": "正在打开录制文件并准备提取帧",
            "processed": 0,
            "total": None,
        }

    def _observe_conversion_output_locked(self, message: str) -> None:
        progress = self._conversion_progress
        if self._task != "convert" or progress is None:
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
                )
            return

        integration = re.search(
            r"integrate rgbd frame\s+(\d+)\s+\((\d+) of (\d+)\)", message
        )
        if integration:
            frame_index = int(integration.group(1))
            local_current = int(integration.group(2))
            local_total = int(integration.group(3))
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
            r"Fragment\s+(\d+)\s+/\s+(\d+)\s+::\s+RGBD matching between frame\s*:\s*(\d+) and (\d+)",
            message,
        )
        if fragment_match and progress.get("stage") == "make":
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
            if not recording.exists() and not dataset.exists() and not partials:
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
        if not self.launcher.is_file() or not os.access(self.launcher, os.X_OK):
            raise WebActionError(f"项目启动器不可执行: {self.launcher}", HTTPStatus.INTERNAL_SERVER_ERROR)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        if environment_overrides:
            environment.update(environment_overrides)
        self._generation += 1
        generation = self._generation
        self._append_log("$ " + shlex.join(command), level="command")
        try:
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
                start_new_session=True,
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
            self._append_log("录制文件已封装完成，可以开始转换。", level="success")
            return
        self._phase = "error"
        if valid:
            self._error = f"录制进程异常退出（代码 {returncode}）；文件已保留，请查看日志"
        else:
            self._error = f"录制失败（退出代码 {returncode}），没有生成有效录制文件"
        self._append_log(self._error, level="error")

    def _finish_conversion_locked(self, returncode: int) -> None:
        self._conversion_finished_at = _now_iso()
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
            self._append_log("转换与四阶段重建完成。", level="success")
            return
        self._phase = "error"
        if self._conversion_progress is not None:
            self._conversion_progress["status"] = "failed"
        if returncode == 0:
            self._error = "重建进程已结束，但未找到最终 integrated.ply"
        else:
            self._error = f"转换/重建失败（退出代码 {returncode}），录制文件仍已保留"
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
            if self._process is not None:
                raise WebActionError("当前任务尚未结束，不能开始新的录制")
            recording, dataset = self._new_paths_locked(name, spec["extension"])
            self._logs.clear()
            self._phase = "recording"
            self._hardware = hardware_id
            self._device = device_index
            self._recording = recording
            self._dataset = dataset
            self._mesh = dataset / "scene" / "integrated.ply"
            self._error = None
            self._recording_started_at = _now_iso()
            self._recording_finished_at = None
            self._conversion_started_at = None
            self._conversion_finished_at = None
            self._conversion_progress = None
            live_dir = self._prepare_live_dir_locked()
            self._touch_locked()
            command = [
                str(self.launcher),
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
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            raise WebActionError(f"无法停止录制进程: {exc}") from exc

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or self._task != "record":
                raise WebActionError("当前没有正在录制的任务")
            if self._phase == "stopping":
                return self._snapshot_locked()
            self._phase = "stopping"
            self._append_log("正在停止录制并封装文件，请稍候……", level="command")
            self._signal_process_group(process, signal.SIGINT)
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

    def start_conversion(self, *, stride: object = 1) -> dict[str, Any]:
        try:
            stride_value = int(stride)
        except (TypeError, ValueError) as exc:
            raise WebActionError("帧步长必须是整数", HTTPStatus.BAD_REQUEST) from exc
        if not 1 <= stride_value <= 1000:
            raise WebActionError("帧步长必须在 1 到 1000 之间", HTTPStatus.BAD_REQUEST)

        with self._lock:
            if self._process is not None:
                raise WebActionError("当前任务尚未结束，不能开始转换")
            if not _is_nonempty_file(self._recording):
                raise WebActionError("没有可转换的录制文件；请先完成录制")
            assert self._recording is not None
            assert self._dataset is not None
            command = [
                str(self.launcher),
                "reconstruct",
                str(self._recording),
                "--dataset",
                str(self._dataset),
                "--stride",
                str(stride_value),
            ]
            if self._dataset.exists():
                command.append("--force-extract")
            self._phase = "converting"
            self._error = None
            self._conversion_started_at = _now_iso()
            self._conversion_finished_at = None
            self._mesh = self._dataset / "scene" / "integrated.ply"
            self._reset_conversion_progress_locked()
            self._append_log(
                "开始提取 RGB-D 帧并执行 make、register、refine、integrate 四阶段重建。",
                level="command",
            )
            self._touch_locked()
            try:
                self._spawn_locked("convert", command)
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

    def _process_paths_locked(self) -> dict[str, Path | None]:
        dataset = self._dataset
        if dataset is None:
            return {"rgb": None, "depth": None, "model": None}
        extracting = dataset.with_name(dataset.name + ".extracting")
        working = dataset if dataset.is_dir() else extracting
        rgb = self._latest_file(working / "color", ("*.jpg", "*.png"))
        depth = self._latest_file(working / "depth", ("*.png",))
        model: Path | None = None
        if _is_nonempty_file(self._mesh):
            model = self._mesh
        else:
            model = self._latest_file(
                working / "fragments",
                ("fragment_optimized_*.ply", "fragment_*.ply"),
            )
        return {"rgb": rgb, "depth": depth, "model": model}

    def _process_snapshot_locked(self) -> dict[str, Any] | None:
        if self._conversion_progress is None:
            return None
        progress = dict(self._conversion_progress)
        paths = self._process_paths_locked()
        stage = progress.get("stage")
        rgb = self._versioned_file(paths["rgb"])
        depth = self._versioned_file(paths["depth"])
        model = self._versioned_file(paths["model"])
        if stage == "extract" and rgb and depth:
            progress["artifact"] = {
                "kind": "rgbd",
                "rgb": rgb,
                "depth": depth,
                "rgb_url": "/api/process/rgb",
                "depth_url": "/api/process/depth",
                "version": f"{rgb['version']}-{depth['version']}",
            }
        elif model:
            progress["artifact"] = {
                "kind": "model",
                "model": model,
                "model_url": "/api/process/model",
                "version": model["version"],
                "label": (
                    "最终融合网格" if paths["model"] == self._mesh else "最新局部片段"
                ),
            }
        elif rgb and depth:
            progress["artifact"] = {
                "kind": "rgbd",
                "rgb": rgb,
                "depth": depth,
                "rgb_url": "/api/process/rgb",
                "depth_url": "/api/process/depth",
                "version": f"{rgb['version']}-{depth['version']}",
            }
        else:
            progress["artifact"] = None
        return progress

    def _snapshot_locked(self) -> dict[str, Any]:
        process = self._process
        recording = _file_summary(self._recording)
        mesh = _file_summary(self._mesh)
        recording_ready = bool(recording and recording["exists"] and recording["size"] > 0)
        mesh_ready = bool(mesh and mesh["exists"] and mesh["size"] > 0)
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
            "recording_started_at": self._recording_started_at,
            "recording_finished_at": self._recording_finished_at,
            "conversion_started_at": self._conversion_started_at,
            "conversion_finished_at": self._conversion_finished_at,
            "can_start_recording": process is None,
            "can_stop_recording": process is not None and self._task == "record" and self._phase == "recording",
            "can_start_conversion": process is None and recording_ready,
            "recording_url": "/api/files/recording" if recording_ready else None,
            "mesh_url": "/api/files/mesh" if mesh_ready else None,
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

    def process_file(self, kind: str) -> Path | None:
        if kind not in {"rgb", "depth", "model"}:
            return None
        with self._lock:
            path = self._process_paths_locked().get(kind)
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
            path = self._mesh if kind == "mesh" else self._recording
            if not _is_nonempty_file(path):
                return None
            assert path is not None
            try:
                path.resolve().relative_to(ROOT.resolve())
            except ValueError:
                return None
            return path.resolve()

    def discover_devices(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._device_lock:
            with self._lock:
                active = self._process is not None
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
                    items[hardware_id]["devices"].append(
                        {
                            "index": index,
                            "name": name,
                            "serial": str(getattr(device, "serial", "")) or None,
                        }
                    )
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
        try:
            if process is None or process.poll() is not None:
                return
            self._append_log("Web 服务正在退出，先停止当前子任务。", level="command")
            self._signal_process_group(process, signal.SIGINT)
            try:
                process.wait(timeout=12)
                return
            except subprocess.TimeoutExpired:
                pass
            self._signal_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
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
            path = controller.result_file(kind)
            if path is None:
                self._error(HTTPStatus.NOT_FOUND, "文件尚未生成或已不存在")
                return
            content_type = "model/ply" if kind == "mesh" else "application/octet-stream"
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self._headers(content_type, size, cache="no-store")
            disposition = "inline" if kind == "mesh" else "attachment"
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
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self._headers(content_type, size, cache="no-store")
            self.end_headers()
            try:
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_visual(self, scope: str, kind: str) -> None:
            path = (
                controller.live_file(kind)
                if scope == "live"
                else controller.process_file(kind)
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
            elif parsed.path == "/api/live/state":
                self._send_json({"ok": True, "live": controller.live_snapshot()})
            elif parsed.path in {"/api/live/rgb", "/api/live/depth"}:
                self._send_visual("live", parsed.path.rsplit("/", 1)[-1])
            elif parsed.path in {
                "/api/process/rgb",
                "/api/process/depth",
                "/api/process/model",
            }:
                self._send_visual("process", parsed.path.rsplit("/", 1)[-1])
            elif parsed.path == "/api/files/recording":
                self._send_file("recording")
            elif parsed.path == "/api/files/mesh":
                self._send_file("mesh")
            else:
                self._error(HTTPStatus.NOT_FOUND, "页面不存在")

        def do_POST(self) -> None:
            if not self._host_is_local():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "仅接受本机访问")
                return
            parsed = urlsplit(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/record/start":
                    state = controller.start_recording(
                        hardware=body.get("hardware"),
                        device=body.get("device", 0),
                        name=body.get("name"),
                    )
                elif parsed.path == "/api/record/stop":
                    state = controller.stop_recording()
                elif parsed.path == "/api/convert/start":
                    state = controller.start_conversion(stride=body.get("stride", 1))
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
