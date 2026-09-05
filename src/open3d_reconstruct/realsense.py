from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from .configuration import read_json_object, write_json
from .extraction import complete_extraction, prepare_extraction
from .paths import DEFAULT_REALSENSE_SENSOR_CONFIG, RECORDINGS_DIR


RECORDING_EXTENSION = ".bag"
SUPPORTED_CONFIG_KEYS = {
    "serial",
    "color_format",
    "color_resolution",
    "depth_format",
    "depth_resolution",
    "fps",
    "visual_preset",
}


def _open3d():
    import open3d as o3d

    if not bool(o3d._build_config.get("BUILD_LIBREALSENSE")):
        raise RuntimeError("当前 Open3D wheel 未启用 librealsense")
    required = ("RealSenseSensor", "RealSenseSensorConfig", "RSBagReader")
    if not all(hasattr(o3d.t.io, name) for name in required):
        raise RuntimeError("当前 Open3D 缺少 RealSense 采集或 BAG 读取接口")
    return o3d


def _device_index(value: int) -> int:
    if not 0 <= value <= 255:
        raise ValueError("设备编号必须在 0 到 255 之间")
    return value


def is_supported_device_name(name: str) -> bool:
    normalized = "".join(character for character in name.upper() if character.isalnum())
    return "D435" in normalized or "DEPTHCAMERA435" in normalized


def device_model(name: str) -> str:
    normalized = "".join(character for character in name.upper() if character.isalnum())
    return "D435i" if "435I" in normalized else "D435"


def enumerate_devices() -> list[Any]:
    o3d = _open3d()
    return list(o3d.t.io.RealSenseSensor.enumerate_devices())


def supported_devices() -> list[Any]:
    return [item for item in enumerate_devices() if is_supported_device_name(item.name)]


def list_devices() -> None:
    o3d = _open3d()
    devices = enumerate_devices()
    if not devices:
        print("librealsense 当前枚举到 0 台 RealSense 设备。")
        return
    print(f"RealSense 设备列表（{len(devices)} 台；本系统适配 D435/D435i）：")
    o3d.t.io.RealSenseSensor.list_devices()
    unsupported_indexes = [
        str(index)
        for index, item in enumerate(devices)
        if not is_supported_device_name(item.name)
    ]
    if unsupported_indexes:
        print(
            f"提示：编号 {', '.join(unsupported_indexes)} "
            "不是本系统已验证的 D435/D435i。"
        )


def _read_sensor_config(path: Path | None) -> tuple[Path, dict[str, str]]:
    config_path = (path or DEFAULT_REALSENSE_SENSOR_CONFIG).expanduser().resolve()
    raw = read_json_object(config_path)
    unknown = sorted(set(raw) - SUPPORTED_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"RealSense 配置包含未知字段: {', '.join(unknown)}")
    non_strings = sorted(key for key, value in raw.items() if not isinstance(value, str))
    if non_strings:
        raise ValueError(
            "RealSense 配置值必须使用 JSON 字符串: " + ", ".join(non_strings)
        )
    return config_path, dict(raw)


def load_sensor_config(path: Path | None = None, *, device: int = 0):
    o3d = _open3d()
    config_path, values = _read_sensor_config(path)
    devices = enumerate_devices()
    serial = values.get("serial", "").strip()
    if serial:
        matches = [item for item in devices if item.serial == serial]
        if not matches:
            raise RuntimeError(f"配置指定的 RealSense 序列号当前未接入: {serial}")
        selected = matches[0]
        values["serial"] = serial
    else:
        device = _device_index(device)
        if not devices:
            raise RuntimeError("librealsense 当前没有枚举到设备；请接入 D435/D435i 后重试")
        if device >= len(devices):
            raise ValueError(
                f"设备编号 {device} 不存在；当前只枚举到 {len(devices)} 台 RealSense"
            )
        selected = devices[device]
        # Open3D 0.19's bundled librealsense config always contains a serial
        # field. Supplying the resolved serial makes --sensor deterministic
        # even when several cameras are connected.
        values["serial"] = selected.serial
    if not is_supported_device_name(selected.name):
        raise RuntimeError(
            f"所选设备是 {selected.name!r}；当前真机适配范围仅为 D435/D435i"
        )
    try:
        config = o3d.t.io.RealSenseSensorConfig(values)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"无法读取 RealSense 配置 {config_path}: {exc}") from exc
    return config, selected


def default_recording_path(name: str | None = None) -> Path:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    if name:
        safe_name = Path(name).name
        if safe_name in {"", ".", ".."}:
            raise ValueError("录制名称无效")
        if not safe_name.lower().endswith(RECORDING_EXTENSION):
            safe_name += RECORDING_EXTENSION
        return RECORDINGS_DIR / safe_name
    timestamp = dt.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return RECORDINGS_DIR / f"{timestamp}{RECORDING_EXTENSION}"


def _update_visualizer(visualizer, displayed, rgbd):
    current = rgbd.to_legacy()
    if displayed is None:
        if not visualizer.add_geometry(current):
            raise RuntimeError("无法把 RealSense RGB-D 图像加入预览窗口")
        return current
    displayed.color = current.color
    displayed.depth = current.depth
    visualizer.update_geometry(displayed)
    return displayed


def preview(
    *,
    sensor_config: Path | None,
    device: int,
    align_depth_to_color: bool,
    no_window: bool,
    frames: int | None,
) -> int:
    o3d = _open3d()
    config, selected = load_sensor_config(sensor_config, device=device)
    frame_limit = frames if frames is not None else (30 if no_window else None)
    if frame_limit is not None and frame_limit <= 0:
        raise ValueError("--frames 必须大于 0")

    sensor = o3d.t.io.RealSenseSensor()
    if not sensor.init_sensor(config, device, ""):
        raise RuntimeError(f"无法初始化 {selected.name}（序列号 {selected.serial}）")
    if not sensor.start_capture(False):
        raise RuntimeError(f"无法启动 {selected.name} 的 RGB-D 数据流")

    visualizer = None
    displayed = None
    stopped = False
    captured = 0
    started = time.monotonic()
    last_frame = started
    try:
        if not no_window:
            visualizer = o3d.visualization.VisualizerWithKeyCallback()

            def stop_callback(_visualizer):
                nonlocal stopped
                stopped = True
                return False

            visualizer.register_key_callback(256, stop_callback)
            if not visualizer.create_window(
                f"RealSense {device_model(selected.name)} 预览", 1280, 480
            ):
                raise RuntimeError("无法创建预览窗口；可使用 --no-window 做无界面采集测试")
            print("预览已启动，按 ESC 或关闭窗口退出。")
        else:
            print(f"无界面采集测试：读取 {frame_limit} 帧。")

        while not stopped and (frame_limit is None or captured < frame_limit):
            rgbd = sensor.capture_frame(True, align_depth_to_color)
            if rgbd.is_empty():
                if time.monotonic() - last_frame > 10:
                    raise RuntimeError("设备已启动，但连续 10 秒未收到同步 RGB-D 帧")
                continue
            last_frame = time.monotonic()
            captured += 1
            if visualizer is not None:
                displayed = _update_visualizer(visualizer, displayed, rgbd)
                if not visualizer.poll_events():
                    break
                visualizer.update_renderer()
    finally:
        sensor.stop_capture()
        if visualizer is not None:
            visualizer.destroy_window()

    elapsed = max(time.monotonic() - started, 1e-9)
    print(
        f"{device_model(selected.name)} 成功读取 {captured} 帧，"
        f"平均 {captured / elapsed:.1f} FPS。"
    )
    if captured == 0:
        raise RuntimeError("设备已连接，但未能读取任何 RGB-D 帧")
    return captured


def record(
    output: Path,
    *,
    sensor_config: Path | None,
    device: int,
    seconds: float | None,
    preview_window: bool,
    align_depth_to_color: bool,
    force: bool,
) -> Path:
    o3d = _open3d()
    config, selected = load_sensor_config(sensor_config, device=device)
    output = output.expanduser().resolve()
    if output.suffix.lower() != RECORDING_EXTENSION:
        raise ValueError("RealSense 录制输出必须使用 .bag 扩展名")
    if seconds is not None and seconds <= 0:
        raise ValueError("--seconds 必须大于 0")
    if output.exists():
        if not force:
            raise FileExistsError(f"输出已存在: {output}（使用 --force 明确覆盖）")
        if not output.is_file():
            raise ValueError(f"拒绝覆盖非普通文件: {output}")
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    sensor = o3d.t.io.RealSenseSensor()
    if not sensor.init_sensor(config, device, str(output)):
        raise RuntimeError(f"无法初始化 {selected.name}（序列号 {selected.serial}）")
    if not sensor.start_capture(True):
        raise RuntimeError(f"无法启动 RealSense BAG 录制: {output}")

    from .live import LivePreviewPublisher

    publisher = LivePreviewPublisher.from_environment()

    recording = True
    stopped = False
    frame_count = 0
    visualizer = None
    displayed = None
    started = time.monotonic()
    last_frame = started
    try:
        if preview_window:
            visualizer = o3d.visualization.VisualizerWithKeyCallback()

            def stop_callback(_visualizer):
                nonlocal stopped
                stopped = True
                return False

            def pause_callback(_visualizer):
                nonlocal recording
                if recording:
                    sensor.pause_record()
                    recording = False
                    print("录制已暂停；再按空格继续。")
                else:
                    sensor.resume_record()
                    recording = True
                    print("继续录制。")
                return False

            visualizer.register_key_callback(256, stop_callback)
            visualizer.register_key_callback(32, pause_callback)
            if not visualizer.create_window(
                f"RealSense {device_model(selected.name)} 录制", 1280, 480
            ):
                raise RuntimeError("无法创建录制窗口；可使用 --no-preview 无界面录制")

        duration_text = f"{seconds:g} 秒" if seconds is not None else "直到 Ctrl+C"
        if preview_window:
            print(f"开始录制 {duration_text}。空格暂停/继续，ESC 保存退出。")
        else:
            print(f"开始无界面录制 {duration_text}。按 Ctrl+C 可提前保存退出。")

        while not stopped:
            if publisher is not None and publisher.stop_requested:
                print("收到 Web 安全停止请求，正在封装并保存 BAG……")
                break
            if seconds is not None and time.monotonic() - started >= seconds:
                break
            rgbd = sensor.capture_frame(True, align_depth_to_color)
            if rgbd.is_empty():
                if publisher is not None and publisher.stop_requested:
                    print("收到 Web 安全停止请求，正在封装并保存 BAG……")
                    break
                if time.monotonic() - last_frame > 10:
                    raise RuntimeError("录制期间连续 10 秒未收到同步 RGB-D 帧")
                continue
            last_frame = time.monotonic()
            if recording:
                frame_count += 1
            if publisher is not None:
                publisher.publish_rgbd(rgbd, frame_count=frame_count)
            if visualizer is not None:
                displayed = _update_visualizer(visualizer, displayed, rgbd)
                if not visualizer.poll_events():
                    break
                visualizer.update_renderer()
    except KeyboardInterrupt:
        print("\n收到中断，正在封装并保存 BAG……")
    finally:
        sensor.stop_capture()
        if publisher is not None:
            publisher.close(frame_count=frame_count)
        if visualizer is not None:
            visualizer.destroy_window()

    if frame_count == 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("录制结束，但 BAG 中没有有效的 RGB-D 帧")
    print(
        f"录制完成：{output}（{frame_count} 帧，"
        f"{output.stat().st_size / 1024**2:.1f} MiB）"
    )
    return output


def _write_intrinsic_and_metadata(o3d, partial: Path, metadata) -> None:
    intrinsic_path = partial / "intrinsic.json"
    if not o3d.io.write_pinhole_camera_intrinsic(
        str(intrinsic_path), metadata.intrinsics
    ):
        raise RuntimeError("无法从 BAG 写出相机内参")
    value = read_json_object(intrinsic_path)
    value.update(
        {
            "device_name": metadata.device_name,
            "serial_number": metadata.serial_number,
            "color_format": metadata.color_format,
            "depth_format": metadata.depth_format,
            "depth_scale": float(metadata.depth_scale),
            "stream_length_usec": int(metadata.stream_length_usec),
            "width": int(metadata.width),
            "height": int(metadata.height),
            "fps": float(metadata.fps),
        }
    )
    write_json(intrinsic_path, value)


def extract_bag(
    source: Path,
    destination: Path,
    *,
    force: bool = False,
    stride: int = 1,
) -> Path:
    o3d = _open3d()
    workspace = prepare_extraction(
        source,
        destination,
        suffix=RECORDING_EXTENSION,
        format_name="RealSense BAG",
        force=force,
        stride=stride,
    )
    if workspace.reused:
        return workspace.destination
    partial = workspace.partial
    assert partial is not None
    (partial / "color").mkdir()
    (partial / "depth").mkdir()

    reader = o3d.t.io.RSBagReader(64)
    if not reader.open(str(workspace.source)) or not reader.is_opened():
        raise RuntimeError(f"RealSense BAG Reader 无法打开: {workspace.source}")

    saved = 0
    seen = 0
    started = time.monotonic()
    metadata = None
    depth_scale = 0.0
    try:
        metadata = reader.metadata
        depth_scale = float(metadata.depth_scale)
        if depth_scale <= 0:
            raise RuntimeError(f"BAG 中的深度比例无效: {depth_scale}")
        _write_intrinsic_and_metadata(o3d, partial, metadata)
        while not reader.is_eof():
            rgbd = reader.next_frame()
            if rgbd.is_empty():
                continue
            current = seen
            seen += 1
            if current % stride:
                continue
            legacy = rgbd.to_legacy()
            color_path = partial / "color" / f"{saved:06d}.jpg"
            depth_path = partial / "depth" / f"{saved:06d}.png"
            if not o3d.io.write_image(str(color_path), legacy.color):
                raise RuntimeError(f"写入彩色帧失败: {color_path}")
            if not o3d.io.write_image(str(depth_path), legacy.depth):
                raise RuntimeError(f"写入深度帧失败: {depth_path}")
            saved += 1
            if saved % 30 == 0:
                print(f"已提取 {saved} 帧……")
    finally:
        reader.close()

    if saved == 0:
        raise RuntimeError("BAG 中没有可读取的同步 RGB-D 帧")
    assert metadata is not None
    complete_extraction(
        workspace,
        frame_count=saved,
        source_frame_count=seen,
        depth_scale=depth_scale,
        manifest_extra={
            "source_format": "RealSense BAG",
            "camera": "realsense",
            "device_name": metadata.device_name,
            "serial_number": metadata.serial_number,
            "fps": float(metadata.fps),
        },
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    print(
        f"提取完成：{workspace.destination}（{saved} 帧，{elapsed:.1f} 秒，"
        f"depth_scale={depth_scale:g}）"
    )
    return workspace.destination
