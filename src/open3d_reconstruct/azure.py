from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from .configuration import read_json_object
from .extraction import complete_extraction, prepare_extraction
from .paths import DEFAULT_SENSOR_CONFIG, RECORDINGS_DIR


RECORDING_EXTENSION = ".mkv"


def _open3d():
    import open3d as o3d

    return o3d


def _device_index(value: int) -> int:
    if not 0 <= value <= 255:
        raise ValueError("设备编号必须在 0 到 255 之间")
    return value


def _ensure_device_available(value: int) -> int:
    from .doctor import k4a_device_count

    value = _device_index(value)
    count = k4a_device_count()
    if count == 0:
        raise RuntimeError("Azure Kinect SDK 当前没有枚举到设备；请接入设备后重试")
    if value >= count:
        raise ValueError(f"设备编号 {value} 不存在；当前只枚举到 {count} 台设备")
    return value


def load_sensor_config(path: Path | None = None):
    o3d = _open3d()
    config_path = (path or DEFAULT_SENSOR_CONFIG).expanduser().resolve()
    read_json_object(config_path)
    config = o3d.io.read_azure_kinect_sensor_config(str(config_path))
    return config


def list_devices() -> None:
    from .doctor import k4a_device_count

    o3d = _open3d()
    count = k4a_device_count()
    if count == 0:
        print("Azure Kinect SDK 当前枚举到 0 台设备。")
        return
    print(f"Azure Kinect SDK 设备列表（{count} 台）：")
    o3d.io.AzureKinectSensor.list_devices()


def default_recording_path(name: str | None = None) -> Path:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    if name:
        safe_name = Path(name).name
        if safe_name in {"", ".", ".."}:
            raise ValueError("录制名称无效")
        if not safe_name.lower().endswith(".mkv"):
            safe_name += ".mkv"
        return RECORDINGS_DIR / safe_name
    timestamp = dt.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return RECORDINGS_DIR / f"{timestamp}.mkv"


def preview(
    *,
    sensor_config: Path | None,
    device: int,
    align_depth_to_color: bool,
    no_window: bool,
    frames: int | None,
) -> int:
    device = _ensure_device_available(device)
    o3d = _open3d()
    sensor = o3d.io.AzureKinectSensor(load_sensor_config(sensor_config))
    if not sensor.connect(device):
        raise RuntimeError(f"无法连接 Azure Kinect 设备 {device}")

    frame_limit = frames if frames is not None else (30 if no_window else None)
    if frame_limit is not None and frame_limit <= 0:
        raise ValueError("--frames 必须大于 0")

    visualizer = None
    geometry_added = False
    stopped = False
    captured = 0
    started = time.monotonic()
    if not no_window:
        visualizer = o3d.visualization.VisualizerWithKeyCallback()

        def stop_callback(_visualizer):
            nonlocal stopped
            stopped = True
            return False

        visualizer.register_key_callback(256, stop_callback)
        if not visualizer.create_window("Azure Kinect 预览", 1920, 540):
            raise RuntimeError("无法创建预览窗口；可使用 --no-window 做无界面采集测试")
        print("预览已启动，按 ESC 或关闭窗口退出。")
    else:
        print(f"无界面采集测试：读取 {frame_limit} 帧。")

    try:
        while not stopped and (frame_limit is None or captured < frame_limit):
            rgbd = sensor.capture_frame(align_depth_to_color)
            if rgbd is None:
                continue
            captured += 1
            if visualizer is not None:
                if not geometry_added:
                    visualizer.add_geometry(rgbd)
                    geometry_added = True
                visualizer.update_geometry(rgbd)
                if not visualizer.poll_events():
                    break
                visualizer.update_renderer()
    finally:
        if visualizer is not None:
            visualizer.destroy_window()

    elapsed = max(time.monotonic() - started, 1e-9)
    print(f"成功读取 {captured} 帧，平均 {captured / elapsed:.1f} FPS。")
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
    device = _ensure_device_available(device)
    o3d = _open3d()
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".mkv":
        raise ValueError("录制输出必须使用 .mkv 扩展名")
    if seconds is not None and seconds <= 0:
        raise ValueError("--seconds 必须大于 0")
    if output.exists():
        if not force:
            raise FileExistsError(f"输出已存在: {output}（使用 --force 明确覆盖）")
        if not output.is_file():
            raise ValueError(f"拒绝覆盖非普通文件: {output}")
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    from .live import LivePreviewPublisher

    publisher = LivePreviewPublisher.from_environment()
    if publisher is not None:
        from .k4a_live import record_with_imu

        try:
            return record_with_imu(
                output,
                sensor_config=sensor_config,
                device=device,
                seconds=seconds,
                publisher=publisher,
            )
        except Exception:
            publisher.close(frame_count=0)
            raise

    recorder = o3d.io.AzureKinectRecorder(
        load_sensor_config(sensor_config), device
    )
    if not recorder.init_sensor():
        raise RuntimeError(f"无法连接 Azure Kinect 设备 {device}")
    if not recorder.open_record(str(output)):
        raise RuntimeError(f"无法创建 MKV 录制文件: {output}")

    recording = True
    stopped = False
    geometry_added = False
    frame_count = 0
    visualizer = None
    if preview_window:
        visualizer = o3d.visualization.VisualizerWithKeyCallback()

        def stop_callback(_visualizer):
            nonlocal stopped
            stopped = True
            return False

        def pause_callback(_visualizer):
            nonlocal recording
            recording = not recording
            print("继续录制。" if recording else "录制已暂停；再按空格继续。")
            return False

        visualizer.register_key_callback(256, stop_callback)
        visualizer.register_key_callback(32, pause_callback)
        if not visualizer.create_window("Azure Kinect 录制", 1920, 540):
            recorder.close_record()
            raise RuntimeError("无法创建录制窗口；可使用 --no-preview 无界面录制")

    started = time.monotonic()
    duration_text = f"{seconds:g} 秒" if seconds is not None else "直到 Ctrl+C"
    if preview_window:
        print(f"开始录制 {duration_text}。空格暂停/继续，ESC 保存退出。")
    else:
        print(f"开始无界面录制 {duration_text}。按 Ctrl+C 可提前保存退出。")

    try:
        while not stopped:
            if seconds is not None and time.monotonic() - started >= seconds:
                break
            rgbd = recorder.record_frame(recording, align_depth_to_color)
            if rgbd is None:
                continue
            if recording:
                frame_count += 1
            if visualizer is not None:
                if not geometry_added:
                    visualizer.add_geometry(rgbd)
                    geometry_added = True
                visualizer.update_geometry(rgbd)
                if not visualizer.poll_events():
                    break
                visualizer.update_renderer()
    except KeyboardInterrupt:
        print("\n收到中断，正在封装并保存 MKV……")
    finally:
        recorder.close_record()
        if visualizer is not None:
            visualizer.destroy_window()

    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("录制结束，但 MKV 文件为空")
    print(f"录制完成：{output}（{frame_count} 帧，{output.stat().st_size / 1024**2:.1f} MiB）")
    return output


def extract_mkv(
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
        suffix=".mkv",
        format_name="MKV",
        force=force,
        stride=stride,
    )
    if workspace.reused:
        return workspace.destination
    source = workspace.source
    destination = workspace.destination
    partial = workspace.partial
    assert partial is not None
    (partial / "color").mkdir()
    (partial / "depth").mkdir()

    reader = o3d.io.AzureKinectMKVReader()
    if not reader.open(str(source)) or not reader.is_opened():
        raise RuntimeError(f"Azure Kinect MKV Reader 无法打开: {source}")

    saved = 0
    seen = 0
    started = time.monotonic()
    try:
        metadata = reader.get_metadata()
        intrinsic_path = partial / "intrinsic.json"
        if not o3d.io.write_azure_kinect_mkv_metadata(str(intrinsic_path), metadata):
            raise RuntimeError("无法从 MKV 写出相机内参")
        while not reader.is_eof():
            rgbd = reader.next_frame()
            if rgbd is None:
                continue
            current = seen
            seen += 1
            if current % stride:
                continue
            color_path = partial / "color" / f"{saved:06d}.jpg"
            depth_path = partial / "depth" / f"{saved:06d}.png"
            if not o3d.io.write_image(str(color_path), rgbd.color):
                raise RuntimeError(f"写入彩色帧失败: {color_path}")
            if not o3d.io.write_image(str(depth_path), rgbd.depth):
                raise RuntimeError(f"写入深度帧失败: {depth_path}")
            saved += 1
            if saved % 30 == 0:
                print(f"已提取 {saved} 帧……")
    finally:
        reader.close()

    if saved == 0:
        raise RuntimeError("MKV 中没有可读取的同步 RGB-D 帧")
    complete_extraction(
        workspace,
        frame_count=saved,
        source_frame_count=seen,
        depth_scale=1000.0,
        manifest_extra={"source_format": "MKV", "camera": "azure-kinect"},
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    print(f"提取完成：{destination}（{saved} 帧，{elapsed:.1f} 秒）")
    return destination
