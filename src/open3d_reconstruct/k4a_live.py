from __future__ import annotations

import ctypes
import time
from pathlib import Path
from typing import Any

from .configuration import read_json_object
from .live import LivePreviewPublisher
from .paths import DEFAULT_AZURE_SENSOR_CONFIG, K4A_LIB_DIR


K4A_RESULT_SUCCEEDED = 0
K4A_WAIT_RESULT_SUCCEEDED = 0
K4A_WAIT_RESULT_TIMEOUT = 2
K4A_IMAGE_FORMAT_COLOR_MJPG = 0
K4A_IMAGE_FORMAT_COLOR_BGRA32 = 3


class K4ADeviceConfiguration(ctypes.Structure):
    _fields_ = [
        ("color_format", ctypes.c_int),
        ("color_resolution", ctypes.c_int),
        ("depth_mode", ctypes.c_int),
        ("camera_fps", ctypes.c_int),
        ("synchronized_images_only", ctypes.c_bool),
        ("depth_delay_off_color_usec", ctypes.c_int32),
        ("wired_sync_mode", ctypes.c_int),
        ("subordinate_delay_off_master_usec", ctypes.c_uint32),
        ("disable_streaming_indicator", ctypes.c_bool),
    ]


class K4AFloat3(ctypes.Union):
    _fields_ = [("v", ctypes.c_float * 3)]


class K4AImuSample(ctypes.Structure):
    _fields_ = [
        ("temperature", ctypes.c_float),
        ("acc_sample", K4AFloat3),
        ("acc_timestamp_usec", ctypes.c_uint64),
        ("gyro_sample", K4AFloat3),
        ("gyro_timestamp_usec", ctypes.c_uint64),
    ]


ENUMS: dict[str, dict[str, int]] = {
    "color_format": {
        "K4A_IMAGE_FORMAT_COLOR_MJPG": 0,
        "K4A_IMAGE_FORMAT_COLOR_NV12": 1,
        "K4A_IMAGE_FORMAT_COLOR_YUY2": 2,
        "K4A_IMAGE_FORMAT_COLOR_BGRA32": 3,
    },
    "color_resolution": {
        "K4A_COLOR_RESOLUTION_OFF": 0,
        "K4A_COLOR_RESOLUTION_720P": 1,
        "K4A_COLOR_RESOLUTION_1080P": 2,
        "K4A_COLOR_RESOLUTION_1440P": 3,
        "K4A_COLOR_RESOLUTION_1536P": 4,
        "K4A_COLOR_RESOLUTION_2160P": 5,
        "K4A_COLOR_RESOLUTION_3072P": 6,
    },
    "depth_mode": {
        "K4A_DEPTH_MODE_OFF": 0,
        "K4A_DEPTH_MODE_NFOV_2X2BINNED": 1,
        "K4A_DEPTH_MODE_NFOV_UNBINNED": 2,
        "K4A_DEPTH_MODE_WFOV_2X2BINNED": 3,
        "K4A_DEPTH_MODE_WFOV_UNBINNED": 4,
        "K4A_DEPTH_MODE_PASSIVE_IR": 5,
    },
    "camera_fps": {
        "K4A_FRAMES_PER_SECOND_5": 0,
        "K4A_FRAMES_PER_SECOND_15": 1,
        "K4A_FRAMES_PER_SECOND_30": 2,
    },
    "wired_sync_mode": {
        "K4A_WIRED_SYNC_MODE_STANDALONE": 0,
        "K4A_WIRED_SYNC_MODE_MASTER": 1,
        "K4A_WIRED_SYNC_MODE_SUBORDINATE": 2,
    },
}


def _enum(config: dict[str, Any], key: str) -> int:
    raw = config.get(key)
    try:
        return ENUMS[key][str(raw)]
    except KeyError as exc:
        raise ValueError(
            f"Azure Kinect 配置 {key} 无效: {raw!r}；可用值: "
            + ", ".join(ENUMS[key])
        ) from exc


def _boolean(config: dict[str, Any], key: str) -> bool:
    raw = config.get(key)
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"Azure Kinect 配置 {key} 必须是 true 或 false")


def native_configuration(path: Path | None = None) -> K4ADeviceConfiguration:
    config_path = (path or DEFAULT_AZURE_SENSOR_CONFIG).expanduser().resolve()
    config = read_json_object(config_path)
    return K4ADeviceConfiguration(
        color_format=_enum(config, "color_format"),
        color_resolution=_enum(config, "color_resolution"),
        depth_mode=_enum(config, "depth_mode"),
        camera_fps=_enum(config, "camera_fps"),
        synchronized_images_only=_boolean(config, "synchronized_images_only"),
        depth_delay_off_color_usec=int(config.get("depth_delay_off_color_usec", 0)),
        wired_sync_mode=_enum(config, "wired_sync_mode"),
        subordinate_delay_off_master_usec=int(
            config.get("subordinate_delay_off_master_usec", 0)
        ),
        disable_streaming_indicator=_boolean(config, "disable_streaming_indicator"),
    )


class K4ALibraries:
    def __init__(self) -> None:
        core_path = K4A_LIB_DIR / "libk4a.so.1.4"
        record_path = K4A_LIB_DIR / "libk4arecord.so.1.4"
        if not core_path.exists() or not record_path.exists():
            raise RuntimeError("项目内 Azure Kinect SDK 运行库不完整")
        self.core = ctypes.CDLL(str(core_path), mode=ctypes.RTLD_GLOBAL)
        self.record = ctypes.CDLL(str(record_path), mode=ctypes.RTLD_GLOBAL)
        self._bind()

    def _bind(self) -> None:
        handle_pointer = ctypes.POINTER(ctypes.c_void_p)
        self.core.k4a_device_open.argtypes = [ctypes.c_uint32, handle_pointer]
        self.core.k4a_device_open.restype = ctypes.c_int
        self.core.k4a_device_close.argtypes = [ctypes.c_void_p]
        self.core.k4a_device_close.restype = None
        self.core.k4a_device_start_cameras.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(K4ADeviceConfiguration),
        ]
        self.core.k4a_device_start_cameras.restype = ctypes.c_int
        self.core.k4a_device_stop_cameras.argtypes = [ctypes.c_void_p]
        self.core.k4a_device_stop_cameras.restype = None
        self.core.k4a_device_start_imu.argtypes = [ctypes.c_void_p]
        self.core.k4a_device_start_imu.restype = ctypes.c_int
        self.core.k4a_device_stop_imu.argtypes = [ctypes.c_void_p]
        self.core.k4a_device_stop_imu.restype = None
        self.core.k4a_device_get_capture.argtypes = [
            ctypes.c_void_p,
            handle_pointer,
            ctypes.c_int32,
        ]
        self.core.k4a_device_get_capture.restype = ctypes.c_int
        self.core.k4a_device_get_imu_sample.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(K4AImuSample),
            ctypes.c_int32,
        ]
        self.core.k4a_device_get_imu_sample.restype = ctypes.c_int
        self.core.k4a_capture_release.argtypes = [ctypes.c_void_p]
        self.core.k4a_capture_release.restype = None
        self.core.k4a_capture_get_color_image.argtypes = [ctypes.c_void_p]
        self.core.k4a_capture_get_color_image.restype = ctypes.c_void_p
        self.core.k4a_capture_get_depth_image.argtypes = [ctypes.c_void_p]
        self.core.k4a_capture_get_depth_image.restype = ctypes.c_void_p
        self.core.k4a_image_release.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_release.restype = None
        self.core.k4a_image_get_buffer.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_buffer.restype = ctypes.POINTER(ctypes.c_uint8)
        self.core.k4a_image_get_size.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_size.restype = ctypes.c_size_t
        self.core.k4a_image_get_format.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_format.restype = ctypes.c_int
        self.core.k4a_image_get_width_pixels.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_width_pixels.restype = ctypes.c_int
        self.core.k4a_image_get_height_pixels.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_height_pixels.restype = ctypes.c_int
        self.core.k4a_image_get_stride_bytes.argtypes = [ctypes.c_void_p]
        self.core.k4a_image_get_stride_bytes.restype = ctypes.c_int

        self.record.k4a_record_create.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            K4ADeviceConfiguration,
            handle_pointer,
        ]
        self.record.k4a_record_create.restype = ctypes.c_int
        self.record.k4a_record_add_imu_track.argtypes = [ctypes.c_void_p]
        self.record.k4a_record_add_imu_track.restype = ctypes.c_int
        self.record.k4a_record_write_header.argtypes = [ctypes.c_void_p]
        self.record.k4a_record_write_header.restype = ctypes.c_int
        self.record.k4a_record_write_capture.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.record.k4a_record_write_capture.restype = ctypes.c_int
        self.record.k4a_record_write_imu_sample.argtypes = [
            ctypes.c_void_p,
            K4AImuSample,
        ]
        self.record.k4a_record_write_imu_sample.restype = ctypes.c_int
        self.record.k4a_record_flush.argtypes = [ctypes.c_void_p]
        self.record.k4a_record_flush.restype = ctypes.c_int
        self.record.k4a_record_close.argtypes = [ctypes.c_void_p]
        self.record.k4a_record_close.restype = None


def _require_success(result: int, operation: str) -> None:
    if result != K4A_RESULT_SUCCEEDED:
        raise RuntimeError(f"Azure Kinect SDK 调用失败: {operation}（代码 {result}）")


def _preview_capture(
    api: K4ALibraries,
    capture: ctypes.c_void_p,
    publisher: LivePreviewPublisher,
    frame_count: int,
) -> None:
    if time.monotonic() - publisher.last_publish < publisher.minimum_interval:
        return
    import cv2
    import numpy as np

    color_handle = api.core.k4a_capture_get_color_image(capture)
    depth_handle = api.core.k4a_capture_get_depth_image(capture)
    if not color_handle or not depth_handle:
        if color_handle:
            api.core.k4a_image_release(color_handle)
        if depth_handle:
            api.core.k4a_image_release(depth_handle)
        return
    try:
        color_format = api.core.k4a_image_get_format(color_handle)
        color_size = api.core.k4a_image_get_size(color_handle)
        color_buffer = ctypes.string_at(
            api.core.k4a_image_get_buffer(color_handle), color_size
        )
        if color_format == K4A_IMAGE_FORMAT_COLOR_MJPG:
            color = cv2.imdecode(np.frombuffer(color_buffer, dtype=np.uint8), cv2.IMREAD_COLOR)
            if color is None:
                raise RuntimeError("无法解码 Azure Kinect MJPEG 彩色帧")
        elif color_format == K4A_IMAGE_FORMAT_COLOR_BGRA32:
            width = api.core.k4a_image_get_width_pixels(color_handle)
            height = api.core.k4a_image_get_height_pixels(color_handle)
            stride = api.core.k4a_image_get_stride_bytes(color_handle)
            rows = np.frombuffer(color_buffer, dtype=np.uint8).reshape(height, stride)
            color = rows[:, : width * 4].reshape(height, width, 4).copy()
        else:
            raise RuntimeError(
                "Web 实时预览目前支持 Azure Kinect MJPG/BGRA32 彩色格式"
            )

        depth_width = api.core.k4a_image_get_width_pixels(depth_handle)
        depth_height = api.core.k4a_image_get_height_pixels(depth_handle)
        depth_stride = api.core.k4a_image_get_stride_bytes(depth_handle)
        depth_size = api.core.k4a_image_get_size(depth_handle)
        depth_buffer = ctypes.string_at(
            api.core.k4a_image_get_buffer(depth_handle), depth_size
        )
        depth_rows = np.frombuffer(depth_buffer, dtype="<u2").reshape(
            depth_height, depth_stride // 2
        )
        depth = depth_rows[:, :depth_width].copy()
        publisher.publish_arrays(
            color,
            depth,
            frame_count=frame_count,
            color_is_bgr=True,
            force=True,
        )
    finally:
        api.core.k4a_image_release(color_handle)
        api.core.k4a_image_release(depth_handle)


def record_with_imu(
    output: Path,
    *,
    sensor_config: Path | None,
    device: int,
    seconds: float | None,
    publisher: LivePreviewPublisher,
) -> Path:
    api = K4ALibraries()
    config = native_configuration(sensor_config)
    device_handle = ctypes.c_void_p()
    recording_handle = ctypes.c_void_p()
    cameras_started = False
    imu_started = False
    recording_created = False
    frame_count = 0
    imu_count = 0
    started = time.monotonic()
    last_frame = started
    fps = {0: 5, 1: 15, 2: 30}.get(config.camera_fps, 30)
    timeout_ms = max(100, round(2000 / fps))

    try:
        _require_success(
            api.core.k4a_device_open(device, ctypes.byref(device_handle)),
            "k4a_device_open",
        )
        _require_success(
            api.core.k4a_device_start_cameras(device_handle, ctypes.byref(config)),
            "k4a_device_start_cameras",
        )
        cameras_started = True
        _require_success(
            api.record.k4a_record_create(
                str(output).encode("utf-8"),
                device_handle,
                config,
                ctypes.byref(recording_handle),
            ),
            "k4a_record_create",
        )
        recording_created = True

        imu_track = api.record.k4a_record_add_imu_track(recording_handle) == 0
        _require_success(
            api.record.k4a_record_write_header(recording_handle),
            "k4a_record_write_header",
        )
        if imu_track and api.core.k4a_device_start_imu(device_handle) == 0:
            imu_started = True
            print("Azure Kinect IMU 已启动，采样将写入 MKV 并显示在 Web 页面。")
        else:
            publisher.set_imu_unavailable("Azure Kinect IMU 启动失败；RGB-D 录制仍继续")
            print("警告: Azure Kinect IMU 启动失败，继续录制 RGB-D。")

        started = time.monotonic()
        last_frame = started
        publisher.begin_capture()
        duration_text = f"{seconds:g} 秒" if seconds is not None else "直到 Ctrl+C"
        print(f"开始 Web RGB-D + IMU 录制 {duration_text}。按 Ctrl+C 可保存退出。")
        while True:
            if seconds is not None and time.monotonic() - started >= seconds:
                break
            capture = ctypes.c_void_p()
            result = api.core.k4a_device_get_capture(
                device_handle, ctypes.byref(capture), timeout_ms
            )
            if result == K4A_WAIT_RESULT_TIMEOUT:
                if time.monotonic() - last_frame > 10:
                    raise RuntimeError("录制期间连续 10 秒未收到 Azure Kinect RGB-D 帧")
                continue
            if result != K4A_WAIT_RESULT_SUCCEEDED or not capture.value:
                raise RuntimeError(f"Azure Kinect 获取帧失败（代码 {result}）")
            try:
                _require_success(
                    api.record.k4a_record_write_capture(recording_handle, capture),
                    "k4a_record_write_capture",
                )
                frame_count += 1
                last_frame = time.monotonic()

                if imu_started:
                    for _ in range(512):
                        sample = K4AImuSample()
                        imu_result = api.core.k4a_device_get_imu_sample(
                            device_handle, ctypes.byref(sample), 0
                        )
                        if imu_result == K4A_WAIT_RESULT_TIMEOUT:
                            break
                        if imu_result != K4A_WAIT_RESULT_SUCCEEDED:
                            raise RuntimeError(
                                f"Azure Kinect IMU 读取失败（代码 {imu_result}）"
                            )
                        _require_success(
                            api.record.k4a_record_write_imu_sample(
                                recording_handle, sample
                            ),
                            "k4a_record_write_imu_sample",
                        )
                        imu_count += 1
                        elapsed = max(time.monotonic() - started, 1e-9)
                        publisher.update_imu(
                            acceleration=tuple(sample.acc_sample.v),
                            gyroscope=tuple(sample.gyro_sample.v),
                            temperature=sample.temperature,
                            timestamp_usec=sample.acc_timestamp_usec,
                            sample_rate_hz=imu_count / elapsed,
                            sample_count=imu_count,
                        )
                try:
                    _preview_capture(api, capture, publisher, frame_count)
                except Exception as exc:
                    publisher.report_error(
                        f"实时预览更新失败: {exc}", frame_count=frame_count
                    )
            finally:
                api.core.k4a_capture_release(capture)
    except KeyboardInterrupt:
        print("\n收到中断，正在封装并保存 MKV……")
    finally:
        if imu_started:
            api.core.k4a_device_stop_imu(device_handle)
        if cameras_started:
            api.core.k4a_device_stop_cameras(device_handle)
        if recording_created:
            print("正在封装 Azure Kinect MKV……")
            flush_result = api.record.k4a_record_flush(recording_handle)
            api.record.k4a_record_close(recording_handle)
            if flush_result != K4A_RESULT_SUCCEEDED:
                print(f"警告: k4a_record_flush 返回 {flush_result}")
        if device_handle.value:
            api.core.k4a_device_close(device_handle)
        publisher.close(frame_count=frame_count)

    if frame_count == 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("录制结束，但 MKV 中没有有效的 RGB-D 帧")
    print(
        f"录制完成：{output}（{frame_count} 帧，{output.stat().st_size / 1024**2:.1f} MiB，"
        f"{imu_count} 个 IMU 样本）"
    )
    return output
