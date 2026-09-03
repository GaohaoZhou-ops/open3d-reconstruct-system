from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any


LIVE_DIR_ENV = "OPEN3D_RECONSTRUCT_LIVE_DIR"
LIVE_HARDWARE_ENV = "OPEN3D_RECONSTRUCT_LIVE_HARDWARE"
LIVE_FPS_ENV = "OPEN3D_RECONSTRUCT_LIVE_FPS"

DEFAULT_PREVIEW_FPS = {
    "azure-kinect": 20.0,
    "d435": 8.0,
    "d435i": 8.0,
}


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _imu_placeholder(hardware: str) -> dict[str, Any]:
    if hardware == "azure-kinect":
        return {
            "supported": True,
            "available": False,
            "status": "starting",
            "message": "正在启动 Azure Kinect IMU…",
        }
    if hardware == "d435i":
        return {
            "supported": True,
            "available": False,
            "status": "unavailable",
            "message": "当前 Open3D RealSense 后端只采集 RGB-D，未启用 D435i 运动流",
        }
    return {
        "supported": False,
        "available": False,
        "status": "unsupported",
        "message": "该相机型号不提供 IMU 数据",
    }


class LivePreviewPublisher:
    """Publishes camera previews through atomic project-local files."""

    def __init__(
        self,
        directory: Path,
        *,
        hardware: str,
        max_fps: float = 10.0,
    ) -> None:
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hardware = hardware
        self.target_fps = min(max(float(max_fps), 0.1), 30.0)
        self.minimum_interval = 1.0 / self.target_fps
        self.sequence = 0
        self.started = time.monotonic()
        self.last_publish = 0.0
        self.preview_processing_ms = 0.0
        self.last_error: str | None = None
        self.closed = False
        self.rgb: dict[str, Any] | None = None
        self.depth: dict[str, Any] | None = None
        self.imu: dict[str, Any] = _imu_placeholder(hardware)
        self._write_state(active=True, frame_count=0, fps=0.0)

    @classmethod
    def from_environment(cls) -> LivePreviewPublisher | None:
        value = os.environ.get(LIVE_DIR_ENV)
        if not value:
            return None
        hardware = os.environ.get(LIVE_HARDWARE_ENV, "unknown")
        configured_fps = os.environ.get(LIVE_FPS_ENV)
        if configured_fps is None:
            max_fps = DEFAULT_PREVIEW_FPS.get(hardware, 8.0)
        else:
            try:
                max_fps = float(configured_fps)
            except ValueError as exc:
                raise ValueError(
                    f"{LIVE_FPS_ENV} 必须是有效数字"
                ) from exc
        return cls(
            Path(value),
            hardware=hardware,
            max_fps=max_fps,
        )

    @property
    def enabled(self) -> bool:
        return True

    def begin_capture(self) -> None:
        self.started = time.monotonic()
        self.last_publish = 0.0

    def set_imu_unavailable(self, message: str) -> None:
        self.imu = {
            "supported": self.hardware in {"azure-kinect", "d435i"},
            "available": False,
            "status": "unavailable",
            "message": message,
        }

    def report_error(self, message: str, *, frame_count: int) -> None:
        self.last_error = message
        self.last_publish = time.monotonic()
        self._write_state(
            active=True,
            frame_count=frame_count,
            fps=frame_count / max(time.monotonic() - self.started, 1e-9),
        )

    def update_imu(
        self,
        *,
        acceleration: tuple[float, float, float],
        gyroscope: tuple[float, float, float],
        temperature: float,
        timestamp_usec: int,
        sample_rate_hz: float,
        sample_count: int,
    ) -> None:
        self.imu = {
            "supported": True,
            "available": True,
            "status": "streaming",
            "message": "Azure Kinect IMU 实时数据",
            "acceleration_m_s2": [float(value) for value in acceleration],
            "acceleration_magnitude": math.sqrt(
                sum(float(value) ** 2 for value in acceleration)
            ),
            "gyroscope_rad_s": [float(value) for value in gyroscope],
            "gyroscope_magnitude": math.sqrt(
                sum(float(value) ** 2 for value in gyroscope)
            ),
            "temperature_c": float(temperature),
            "timestamp_usec": int(timestamp_usec),
            "sample_rate_hz": float(sample_rate_hz),
            "sample_count": int(sample_count),
        }

    @staticmethod
    def _resize(image, *, maximum_width: int):
        if image.shape[1] <= maximum_width:
            return image
        import cv2

        scale = maximum_width / image.shape[1]
        return cv2.resize(
            image,
            (maximum_width, max(1, round(image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _depth_preview(depth):
        import cv2
        import numpy as np

        values = np.asarray(depth)
        if values.ndim == 3:
            values = values[..., 0]
        values = values.astype(np.float32, copy=False)
        valid = np.isfinite(values) & (values >= 300.0) & (values <= 3000.0)
        normalized = np.zeros(values.shape, dtype=np.uint8)
        if valid.any():
            clipped = np.clip(values, 300.0, 3000.0)
            normalized[valid] = np.asarray(
                255.0 - ((clipped[valid] - 300.0) * (255.0 / 2700.0)),
                dtype=np.uint8,
            )
        colored = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
        colored[~valid] = 0
        return colored, valid, values

    def _write_state(
        self,
        *,
        active: bool,
        frame_count: int,
        fps: float,
        rgb: dict[str, Any] | None = None,
        depth: dict[str, Any] | None = None,
    ) -> None:
        if rgb is not None:
            self.rgb = rgb
        if depth is not None:
            self.depth = depth
        state: dict[str, Any] = {
            "active": active,
            "hardware": self.hardware,
            "sequence": self.sequence,
            "frame_count": int(frame_count),
            "fps": float(fps),
            "preview_fps": self.sequence
            / max(time.monotonic() - self.started, 1e-9),
            "preview_target_fps": self.target_fps,
            "preview_processing_ms": self.preview_processing_ms,
            "updated_at": _iso_now(),
            "rgb": self.rgb,
            "depth": self.depth,
            "imu": self.imu,
            "error": self.last_error,
        }
        _atomic_json(self.directory / "state.json", state)

    def _encode_depth(self, depth) -> tuple[bytes, dict[str, Any]]:
        import cv2

        depth_bgr, valid, depth_values = self._depth_preview(depth)
        depth_bgr = self._resize(depth_bgr, maximum_width=640)
        depth_ok, depth_encoded = cv2.imencode(
            ".jpg", depth_bgr, [cv2.IMWRITE_JPEG_QUALITY, 88]
        )
        if not depth_ok:
            raise RuntimeError("OpenCV 无法编码深度预览")
        valid_values = depth_values[valid]
        return depth_encoded.tobytes(), {
            "width": int(depth_values.shape[1]),
            "height": int(depth_values.shape[0]),
            "valid_ratio": float(valid.mean()),
            "min_mm": float(valid_values.min()) if valid_values.size else None,
            "max_mm": float(valid_values.max()) if valid_values.size else None,
            "aligned_to_color": False,
        }

    def _publish_encoded(
        self,
        color_payload: bytes,
        depth_payload: bytes,
        *,
        frame_count: int,
        rgb: dict[str, Any],
        depth: dict[str, Any],
        processing_started: float,
    ) -> None:
        _atomic_write(self.directory / "rgb.jpg", color_payload)
        _atomic_write(self.directory / "depth.jpg", depth_payload)
        self.sequence += 1
        self.last_publish = time.monotonic()
        self.preview_processing_ms = (
            time.monotonic() - processing_started
        ) * 1000.0
        elapsed = max(self.last_publish - self.started, 1e-9)
        self.last_error = None
        self._write_state(
            active=True,
            frame_count=frame_count,
            fps=frame_count / elapsed,
            rgb=rgb,
            depth=depth,
        )

    def _publish_failure(
        self,
        error: Exception,
        *,
        frame_count: int,
        processing_started: float,
    ) -> None:
        now = time.monotonic()
        self.last_error = f"实时预览更新失败: {error}"
        self.last_publish = now
        self.preview_processing_ms = (now - processing_started) * 1000.0
        self._write_state(
            active=True,
            frame_count=frame_count,
            fps=frame_count / max(now - self.started, 1e-9),
        )

    def publish_arrays(
        self,
        color,
        depth,
        *,
        frame_count: int,
        color_is_bgr: bool = False,
        force: bool = False,
    ) -> bool:
        processing_started = time.monotonic()
        if (
            not force
            and processing_started - self.last_publish < self.minimum_interval
        ):
            return False
        try:
            import cv2
            import numpy as np

            color_array = np.asarray(color)
            if color_array.ndim == 2:
                color_array = cv2.cvtColor(color_array, cv2.COLOR_GRAY2BGR)
            elif color_array.ndim != 3 or color_array.shape[2] not in {3, 4}:
                raise ValueError(f"不支持的彩色图像形状: {color_array.shape}")
            elif color_array.shape[2] == 4:
                conversion = cv2.COLOR_BGRA2BGR if color_is_bgr else cv2.COLOR_RGBA2BGR
                color_array = cv2.cvtColor(color_array, conversion)
            elif not color_is_bgr:
                color_array = cv2.cvtColor(color_array, cv2.COLOR_RGB2BGR)

            color_array = self._resize(color_array, maximum_width=960)
            color_ok, color_encoded = cv2.imencode(
                ".jpg", color_array, [cv2.IMWRITE_JPEG_QUALITY, 82]
            )
            if not color_ok:
                raise RuntimeError("OpenCV 无法编码彩色预览")
            depth_payload, depth_summary = self._encode_depth(depth)
            self._publish_encoded(
                color_encoded.tobytes(),
                depth_payload,
                frame_count=frame_count,
                rgb={
                    "width": int(color_array.shape[1]),
                    "height": int(color_array.shape[0]),
                },
                depth=depth_summary,
                processing_started=processing_started,
            )
            return True
        except Exception as exc:
            self._publish_failure(
                exc,
                frame_count=frame_count,
                processing_started=processing_started,
            )
            return False

    def publish_jpeg_depth(
        self,
        color_jpeg: bytes,
        depth,
        *,
        frame_count: int,
        color_width: int,
        color_height: int,
        force: bool = False,
    ) -> bool:
        """Publishes an existing camera JPEG without decoding and re-encoding it."""

        processing_started = time.monotonic()
        if (
            not force
            and processing_started - self.last_publish < self.minimum_interval
        ):
            return False
        try:
            color_payload = bytes(color_jpeg)
            if not color_payload.startswith(b"\xff\xd8"):
                raise ValueError("Azure Kinect 彩色帧不是有效的 JPEG 数据")
            if color_width <= 0 or color_height <= 0:
                raise ValueError("Azure Kinect 彩色帧尺寸无效")
            depth_payload, depth_summary = self._encode_depth(depth)
            self._publish_encoded(
                color_payload,
                depth_payload,
                frame_count=frame_count,
                rgb={
                    "width": int(color_width),
                    "height": int(color_height),
                    "passthrough": True,
                },
                depth=depth_summary,
                processing_started=processing_started,
            )
            return True
        except Exception as exc:
            self._publish_failure(
                exc,
                frame_count=frame_count,
                processing_started=processing_started,
            )
            return False

    def publish_rgbd(self, rgbd, *, frame_count: int, force: bool = False) -> bool:
        import numpy as np

        legacy = rgbd.to_legacy() if hasattr(rgbd, "to_legacy") else rgbd
        return self.publish_arrays(
            np.asarray(legacy.color),
            np.asarray(legacy.depth),
            frame_count=frame_count,
            force=force,
        )

    def close(self, *, frame_count: int) -> None:
        if self.closed:
            return
        self.closed = True
        self._write_state(
            active=False,
            frame_count=frame_count,
            fps=frame_count / max(time.monotonic() - self.started, 1e-9),
        )
