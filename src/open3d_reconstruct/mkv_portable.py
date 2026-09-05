from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .configuration import write_json
from .extraction import complete_extraction, prepare_extraction


DEPTH_MODE_INFO: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int]]] = {
    # output resolution: (calibration binned resolution, crop offset)
    (320, 288): ((512, 512), (96, 90)),
    (640, 576): ((1024, 1024), (192, 180)),
    (512, 512): ((512, 512), (0, 0)),
    (1024, 1024): ((1024, 1024), (0, 0)),
}
COLOR_MODE_INFO: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int]]] = {
    (1280, 720): ((1280, 960), (0, 120)),
    (1920, 1080): ((1920, 1440), (0, 180)),
    (2560, 1440): ((2560, 1920), (0, 240)),
    (2048, 1536): ((2048, 1536), (0, 0)),
    (3840, 2160): ((3840, 2880), (0, 360)),
    (4096, 3072): ((4096, 3072), (0, 0)),
}


@dataclass(frozen=True)
class VideoStream:
    index: int
    width: int
    height: int


@dataclass(frozen=True)
class MKVProbe:
    color: VideoStream
    depth: VideoStream
    tags: dict[str, str]


@dataclass(frozen=True)
class CameraCalibration:
    width: int
    height: int
    matrix: Any
    distortion: Any
    parameters: Any
    model: str
    metric_radius: float
    rotation: Any
    translation_mm: Any


def _tools() -> tuple[str, str | None]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            ffmpeg = get_ffmpeg_exe()
        except (ImportError, OSError, RuntimeError):
            ffmpeg = None
    if not ffmpeg:
        raise RuntimeError(
            "便携式 Azure Kinect MKV 提取需要 FFmpeg；请重新运行项目安装器，"
            "macOS 也可运行 brew install ffmpeg"
        )
    return ffmpeg, ffprobe


def _run_json(command: list[str], *, description: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法{description}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法{description}: {detail or f'退出代码 {result.returncode}'}")
    try:
        value = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{description}返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{description}返回的不是 JSON 对象")
    return value


def _stream_is(stream: dict[str, Any], kind: str) -> bool:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        tags = {}
    title = str(tags.get("title", "")).upper()
    return title == kind.upper() or f"K4A_{kind.upper()}_TRACK" in tags


def _build_probe(
    streams: list[dict[str, Any]], tags: dict[str, str]
) -> MKVProbe:
    def find(kind: str) -> VideoStream:
        for item in streams:
            if not isinstance(item, dict) or not _stream_is(item, kind):
                continue
            try:
                stream = VideoStream(
                    index=int(item["index"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"MKV 的 {kind} 流尺寸无效") from exc
            if stream.width <= 0 or stream.height <= 0:
                raise RuntimeError(f"MKV 的 {kind} 流尺寸无效")
            return stream
        raise RuntimeError(f"MKV 中缺少 Azure Kinect {kind} 流")

    return MKVProbe(color=find("color"), depth=find("depth"), tags=tags)


_FFMPEG_VIDEO_STREAM = re.compile(
    r"^\s*Stream #\d+:(?P<index>\d+)(?:\[[^]]+\])?(?:\([^)]*\))?:\s+Video:.*?"
    r"(?P<width>\d{2,5})x(?P<height>\d{2,5})(?:[,\s])"
)
_FFMPEG_METADATA = re.compile(r"^\s*(?P<key>[A-Za-z0-9_]+)\s*:\s*(?P<value>.*?)\s*$")


def _probe_mkv_with_ffmpeg(source: Path, ffmpeg: str) -> MKVProbe:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法读取 MKV 流信息: {exc}") from exc

    streams: list[dict[str, Any]] = []
    tags: dict[str, str] = {}
    current: dict[str, Any] | None = None
    for line in result.stderr.splitlines():
        stream_match = _FFMPEG_VIDEO_STREAM.match(line)
        if stream_match:
            current = {
                "index": int(stream_match.group("index")),
                "width": int(stream_match.group("width")),
                "height": int(stream_match.group("height")),
                "tags": {},
            }
            streams.append(current)
            continue
        if line.lstrip().startswith("Stream #"):
            current = None
            continue
        metadata_match = _FFMPEG_METADATA.match(line)
        if not metadata_match:
            continue
        key = metadata_match.group("key")
        value = metadata_match.group("value")
        if not value:
            continue
        if current is None:
            tags[key] = value
        else:
            current["tags"][key] = value

    for stream in streams:
        tags.update(stream["tags"])
    try:
        return _build_probe(streams, tags)
    except RuntimeError as exc:
        detail = result.stderr.strip()
        if not streams and detail:
            raise RuntimeError(f"无法读取 MKV 流信息: {detail}") from exc
        raise


def probe_mkv(
    source: Path, ffprobe: str | None, *, ffmpeg: str | None = None
) -> MKVProbe:
    if ffprobe is None:
        if ffmpeg is None:
            raise RuntimeError("读取 MKV 流信息需要 ffprobe 或 ffmpeg")
        return _probe_mkv_with_ffmpeg(source, ffmpeg)
    value = _run_json(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        description="读取 MKV 流信息",
    )
    streams = value.get("streams")
    if not isinstance(streams, list):
        raise RuntimeError("MKV 中没有流信息")

    format_value = value.get("format")
    raw_tags = format_value.get("tags", {}) if isinstance(format_value, dict) else {}
    tags: dict[str, str] = (
        {str(key): str(item) for key, item in raw_tags.items()}
        if isinstance(raw_tags, dict)
        else {}
    )
    # Resolution/mode metadata is attached to the individual K4A video tracks,
    # while serial number and firmware metadata live on the Matroska container.
    for stream in streams:
        if not isinstance(stream, dict) or not isinstance(stream.get("tags"), dict):
            continue
        tags.update({str(key): str(item) for key, item in stream["tags"].items()})
    return _build_probe(streams, tags)


def read_calibration(source: Path, ffmpeg: str) -> dict[str, Any]:
    # Azure Kinect recordings store their factory calibration as attachment 0.
    # -t 0 makes FFmpeg stop after parsing the Matroska header and attachment.
    return _run_json(
        [
            ffmpeg,
            "-v",
            "error",
            "-dump_attachment:t:0",
            "pipe:1",
            "-i",
            str(source),
            "-t",
            "0",
            "-f",
            "null",
            "-",
        ],
        description="读取 MKV 内嵌 Azure Kinect 标定",
    )


def _camera_from_calibration(value: dict[str, Any], purpose: str) -> dict[str, Any]:
    try:
        cameras = value["CalibrationInformation"]["Cameras"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Azure Kinect 标定缺少 Cameras") from exc
    if not isinstance(cameras, list):
        raise RuntimeError("Azure Kinect 标定 Cameras 格式无效")
    for camera in cameras:
        if isinstance(camera, dict) and str(camera.get("Purpose", "")).endswith(purpose):
            return camera
    raise RuntimeError(f"Azure Kinect 标定缺少 {purpose} 相机")


def _legacy_color_calibration(camera: dict[str, Any]) -> dict[str, Any]:
    """Convert the SDK's old 16:9 raw color calibration to its 4:3 form."""
    width = int(camera["SensorWidth"])
    height = int(camera["SensorHeight"])
    if width * 9 // 16 != height:
        return camera
    if (width, height) != (4096, 2304):
        raise RuntimeError(f"不支持的旧版彩色标定尺寸: {width}x{height}")
    converted = dict(camera)
    converted["SensorWidth"] = 4096
    converted["SensorHeight"] = 3072
    intrinsics = dict(camera["Intrinsics"])
    parameters = list(intrinsics["ModelParameters"])
    parameters[1] = (parameters[1] * 2304 + 384) / 3072
    parameters[3] = parameters[3] * 2304 / 3072
    intrinsics["ModelParameters"] = parameters
    converted["Intrinsics"] = intrinsics
    return converted


def mode_specific_calibration(
    camera: dict[str, Any], *, width: int, height: int, kind: str
) -> CameraCalibration:
    import numpy as np

    if kind == "color":
        camera = _legacy_color_calibration(camera)
        mode_info = COLOR_MODE_INFO.get((width, height))
    elif kind == "depth":
        mode_info = DEPTH_MODE_INFO.get((width, height))
    else:
        raise ValueError(f"未知相机标定类型: {kind}")
    if mode_info is None:
        raise RuntimeError(f"不支持的 Azure Kinect {kind} 分辨率: {width}x{height}")

    try:
        intrinsics = camera["Intrinsics"]
        raw_parameters = intrinsics["ModelParameters"]
        model = str(intrinsics["ModelType"])
        parameters = np.asarray(raw_parameters, dtype=np.float64).copy()
        rotation = np.asarray(camera["Rt"]["Rotation"], dtype=np.float64).reshape(3, 3)
        translation_mm = (
            np.asarray(camera["Rt"]["Translation"], dtype=np.float64).reshape(3)
            * 1000.0
        )
        metric_radius = float(camera.get("MetricRadius", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Azure Kinect {kind} 标定格式无效") from exc
    if parameters.size < 14:
        raise RuntimeError(f"Azure Kinect {kind} 标定参数不足 14 个")
    if "BrownConrady" not in model and "Rational6KT" not in model:
        raise RuntimeError(f"不支持的 Azure Kinect 畸变模型: {model}")

    binned, crop = mode_info
    parameters[0] = parameters[0] * binned[0] - crop[0] - 0.5
    parameters[1] = parameters[1] * binned[1] - crop[1] - 0.5
    parameters[2] *= binned[0]
    parameters[3] *= binned[1]
    if parameters[2] <= 0 or parameters[3] <= 0:
        raise RuntimeError(f"Azure Kinect {kind} 焦距无效")
    if metric_radius <= 0.0001:
        metric_radius = 1.7

    matrix = np.array(
        [
            [parameters[2], 0.0, parameters[0]],
            [0.0, parameters[3], parameters[1]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    # OpenCV order is k1, k2, p1, p2, k3, k4, k5, k6. The K4A
    # calibration blob stores p2 before p1.
    distortion = np.array(
        [
            parameters[4],
            parameters[5],
            parameters[13],
            parameters[12],
            parameters[6],
            parameters[7],
            parameters[8],
            parameters[9],
        ],
        dtype=np.float64,
    )
    return CameraCalibration(
        width=width,
        height=height,
        matrix=matrix,
        distortion=distortion,
        parameters=parameters,
        model=model,
        metric_radius=metric_radius,
        rotation=rotation,
        translation_mm=translation_mm,
    )


def _project_normalized(
    camera: CameraCalibration, x: Any, y: Any
) -> tuple[Any, Any, Any]:
    """Apply the K4A Rational6KT/Brown-Conrady projection exactly."""
    import numpy as np

    parameters = camera.parameters
    xp = x - parameters[10]
    yp = y - parameters[11]
    radius_squared = xp * xp + yp * yp
    radius_fourth = radius_squared * radius_squared
    radius_sixth = radius_fourth * radius_squared
    numerator = (
        1.0
        + parameters[4] * radius_squared
        + parameters[5] * radius_fourth
        + parameters[6] * radius_sixth
    )
    denominator = (
        1.0
        + parameters[7] * radius_squared
        + parameters[8] * radius_fourth
        + parameters[9] * radius_sixth
    )
    # The K4A SDK uses numerator unchanged when the denominator is zero.
    radial = numerator.copy()
    np.divide(numerator, denominator, out=radial, where=denominator != 0)
    projected_x = xp * radial
    projected_y = yp * radial
    xy = xp * yp
    if "Rational6KT" in camera.model:
        projected_x += (
            (radius_squared + 2.0 * xp * xp) * parameters[12]
            + xy * parameters[13]
        )
        projected_y += (
            (radius_squared + 2.0 * yp * yp) * parameters[13]
            + xy * parameters[12]
        )
    else:
        projected_x += (
            (radius_squared + 2.0 * xp * xp) * parameters[12]
            + 2.0 * xy * parameters[13]
        )
        projected_y += (
            (radius_squared + 2.0 * yp * yp) * parameters[13]
            + 2.0 * xy * parameters[12]
        )
    map_x = (projected_x + parameters[10]) * parameters[2] + parameters[0]
    map_y = (projected_y + parameters[11]) * parameters[3] + parameters[1]
    valid = radius_squared <= camera.metric_radius**2
    valid &= np.isfinite(map_x) & np.isfinite(map_y)
    return map_x, map_y, valid


class RGBDAligner:
    def __init__(self, depth: CameraCalibration, color: CameraCalibration) -> None:
        import cv2
        import numpy as np

        self.depth = depth
        self.color = color
        size = (depth.width, depth.height)
        new_matrix, _roi = cv2.getOptimalNewCameraMatrix(
            depth.matrix,
            depth.distortion,
            size,
            0.0,
            size,
        )
        self.intrinsic = np.asarray(new_matrix, dtype=np.float64)
        yy, xx = np.mgrid[0 : depth.height, 0 : depth.width]
        self.ray_x = (xx.astype(np.float64) - self.intrinsic[0, 2]) / self.intrinsic[0, 0]
        self.ray_y = (yy.astype(np.float64) - self.intrinsic[1, 2]) / self.intrinsic[1, 1]
        depth_map_x, depth_map_y, depth_valid = _project_normalized(
            depth, self.ray_x, self.ray_y
        )
        depth_valid &= (depth_map_x >= 0.0) & (depth_map_x < depth.width - 1)
        depth_valid &= (depth_map_y >= 0.0) & (depth_map_y < depth.height - 1)
        self.depth_valid = depth_valid
        self.depth_map_x = np.where(depth_valid, depth_map_x, -1.0).astype(np.float32)
        self.depth_map_y = np.where(depth_valid, depth_map_y, -1.0).astype(np.float32)

        # Camera Rt values map the device calibration coordinate system into
        # each camera. Compose depth -> device -> color as the K4A SDK does.
        self.rotation = color.rotation @ depth.rotation.T
        self.translation = color.translation_mm - self.rotation @ depth.translation_mm
        self.x_coefficient = (
            self.rotation[0, 0] * self.ray_x
            + self.rotation[0, 1] * self.ray_y
            + self.rotation[0, 2]
        )
        self.y_coefficient = (
            self.rotation[1, 0] * self.ray_x
            + self.rotation[1, 1] * self.ray_y
            + self.rotation[1, 2]
        )
        self.z_coefficient = (
            self.rotation[2, 0] * self.ray_x
            + self.rotation[2, 1] * self.ray_y
            + self.rotation[2, 2]
        )

    def align(self, color_image: Any, depth_image: Any) -> tuple[Any, Any]:
        import cv2
        import numpy as np

        undistorted_depth = cv2.remap(
            depth_image,
            self.depth_map_x,
            self.depth_map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        depth_float = undistorted_depth.astype(np.float64)
        x_color = self.x_coefficient * depth_float + self.translation[0]
        y_color = self.y_coefficient * depth_float + self.translation[1]
        z_color = self.z_coefficient * depth_float + self.translation[2]
        valid = (undistorted_depth > 0) & (z_color > 1e-6)

        x = np.zeros_like(z_color)
        y = np.zeros_like(z_color)
        np.divide(x_color, z_color, out=x, where=valid)
        np.divide(y_color, z_color, out=y, where=valid)
        map_x, map_y, color_valid = _project_normalized(self.color, x, y)
        valid &= self.depth_valid & color_valid
        valid &= (map_x >= 0.0) & (map_x < self.color.width - 1)
        valid &= (map_y >= 0.0) & (map_y < self.color.height - 1)
        map_x = np.where(valid, map_x, -1.0).astype(np.float32)
        map_y = np.where(valid, map_y, -1.0).astype(np.float32)
        aligned_color = cv2.remap(
            color_image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        aligned_depth = undistorted_depth.copy()
        aligned_depth[~valid] = 0
        return aligned_color, aligned_depth


def _read_frame(stream: BinaryIO, size: int, label: str) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining == size:
        return None
    if remaining:
        raise RuntimeError(f"FFmpeg 的 {label} 流在一帧中途结束")
    return b"".join(chunks)


def _decoder_command(
    ffmpeg: str, source: Path, stream: VideoStream, pixel_format: str
) -> list[str]:
    return [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        f"0:{stream.index}",
        "-an",
        "-sn",
        "-dn",
        "-vsync",
        "0",
        "-pix_fmt",
        pixel_format,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def _stderr(error_file: BinaryIO) -> str:
    error_file.seek(0)
    return error_file.read().decode("utf-8", errors="replace").strip()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def extract_mkv_portable(
    source: Path,
    destination: Path,
    *,
    force: bool = False,
    stride: int = 1,
) -> Path:
    import cv2
    import numpy as np

    ffmpeg, ffprobe = _tools()
    workspace = prepare_extraction(
        source,
        destination,
        suffix=".mkv",
        format_name="Azure Kinect MKV",
        force=force,
        stride=stride,
    )
    if workspace.reused:
        return workspace.destination
    partial = workspace.partial
    assert partial is not None
    (partial / "color").mkdir()
    (partial / "depth").mkdir()

    probe = probe_mkv(workspace.source, ffprobe, ffmpeg=ffmpeg)
    calibration_json = read_calibration(workspace.source, ffmpeg)
    depth_camera = mode_specific_calibration(
        _camera_from_calibration(calibration_json, "Depth"),
        width=probe.depth.width,
        height=probe.depth.height,
        kind="depth",
    )
    color_camera = mode_specific_calibration(
        _camera_from_calibration(calibration_json, "PhotoVideo"),
        width=probe.color.width,
        height=probe.color.height,
        kind="color",
    )
    aligner = RGBDAligner(depth_camera, color_camera)
    write_json(partial / "azure-calibration.json", calibration_json)
    intrinsic = aligner.intrinsic
    write_json(
        partial / "intrinsic.json",
        {
            "width": probe.depth.width,
            "height": probe.depth.height,
            "intrinsic_matrix": [
                float(intrinsic[0, 0]),
                0.0,
                0.0,
                0.0,
                float(intrinsic[1, 1]),
                0.0,
                float(intrinsic[0, 2]),
                float(intrinsic[1, 2]),
                1.0,
            ],
            "serial_number": probe.tags.get("K4A_DEVICE_SERIAL_NUMBER", ""),
            "color_mode": probe.tags.get("K4A_COLOR_MODE", ""),
            "depth_mode": probe.tags.get("K4A_DEPTH_MODE", ""),
            "source_color_width": probe.color.width,
            "source_color_height": probe.color.height,
            "alignment": "color-to-undistorted-depth",
        },
    )

    color_frame_size = probe.color.width * probe.color.height * 3
    depth_frame_size = probe.depth.width * probe.depth.height * 2
    saved = 0
    seen = 0
    started = time.monotonic()
    with tempfile.TemporaryFile() as color_error, tempfile.TemporaryFile() as depth_error:
        color_process: subprocess.Popen[bytes] | None = None
        depth_process: subprocess.Popen[bytes] | None = None
        try:
            color_process = subprocess.Popen(
                _decoder_command(ffmpeg, workspace.source, probe.color, "bgr24"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=color_error,
            )
            depth_process = subprocess.Popen(
                _decoder_command(ffmpeg, workspace.source, probe.depth, "gray16le"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=depth_error,
            )
        except OSError as exc:
            if color_process is not None:
                _stop_process(color_process)
            raise RuntimeError(f"无法启动 FFmpeg MKV 解码器: {exc}") from exc
        assert color_process is not None
        assert depth_process is not None
        assert color_process.stdout is not None
        assert depth_process.stdout is not None
        try:
            while True:
                color_bytes = _read_frame(color_process.stdout, color_frame_size, "COLOR")
                depth_bytes = _read_frame(depth_process.stdout, depth_frame_size, "DEPTH")
                if color_bytes is None and depth_bytes is None:
                    break
                if color_bytes is None or depth_bytes is None:
                    raise RuntimeError(
                        "MKV 的 COLOR 与 DEPTH 帧数不一致，无法安全配对 RGB-D"
                    )
                current = seen
                seen += 1
                if current % stride:
                    continue
                color_image = np.frombuffer(color_bytes, dtype=np.uint8).reshape(
                    probe.color.height, probe.color.width, 3
                )
                depth_image = np.frombuffer(depth_bytes, dtype="<u2").reshape(
                    probe.depth.height, probe.depth.width
                )
                aligned_color, aligned_depth = aligner.align(color_image, depth_image)
                color_path = partial / "color" / f"{saved:06d}.jpg"
                depth_path = partial / "depth" / f"{saved:06d}.png"
                if not cv2.imwrite(
                    str(color_path), aligned_color, [cv2.IMWRITE_JPEG_QUALITY, 95]
                ):
                    raise RuntimeError(f"写入彩色帧失败: {color_path}")
                if not cv2.imwrite(
                    str(depth_path), aligned_depth, [cv2.IMWRITE_PNG_COMPRESSION, 3]
                ):
                    raise RuntimeError(f"写入深度帧失败: {depth_path}")
                saved += 1
                if saved % 30 == 0:
                    print(f"已提取并标定对齐 {saved} 帧……", flush=True)
            color_process.stdout.close()
            depth_process.stdout.close()
            color_returncode = color_process.wait()
            depth_returncode = depth_process.wait()
            if color_returncode != 0:
                raise RuntimeError(
                    "FFmpeg COLOR 解码失败: "
                    + (_stderr(color_error) or f"退出代码 {color_returncode}")
                )
            if depth_returncode != 0:
                raise RuntimeError(
                    "FFmpeg DEPTH 解码失败: "
                    + (_stderr(depth_error) or f"退出代码 {depth_returncode}")
                )
        finally:
            _stop_process(color_process)
            _stop_process(depth_process)

    if saved == 0:
        raise RuntimeError("MKV 中没有可读取的同步 RGB-D 帧")
    complete_extraction(
        workspace,
        frame_count=saved,
        source_frame_count=seen,
        depth_scale=1000.0,
        manifest_extra={
            "source_format": "Azure Kinect MKV",
            "camera": "azure-kinect",
            "extraction_backend": "ffmpeg-calibrated",
            "alignment": "color-to-undistorted-depth",
            "source_color_resolution": [probe.color.width, probe.color.height],
            "source_depth_resolution": [probe.depth.width, probe.depth.height],
        },
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    print(
        f"提取完成：{workspace.destination}（{saved} 帧，{elapsed:.1f} 秒，"
        "FFmpeg 标定后端）"
    )
    return workspace.destination
