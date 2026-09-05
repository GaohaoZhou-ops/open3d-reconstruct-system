from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from . import __version__
from .paths import (
    DATASETS_DIR,
    DEFAULT_RECONSTRUCTION_CONFIG,
    DEFAULT_AZURE_SENSOR_CONFIG,
    DEFAULT_REALSENSE_SENSOR_CONFIG,
    ensure_local_directories,
)


CAMERA_ALIASES = {
    "azure": "azure-kinect",
    "azure-kinect": "azure-kinect",
    "k4a": "azure-kinect",
    "kinect": "azure-kinect",
    "realsense": "realsense",
    "rs": "realsense",
    "d435": "realsense",
    "d435i": "realsense",
    "all": "all",
}


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _camera(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    try:
        return CAMERA_ALIASES[normalized]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            f"未知相机类型 {value!r}；可用值: azure-kinect, realsense"
        ) from exc


def _add_camera_option(
    parser: argparse.ArgumentParser, *, default: str, allow_all: bool = False
) -> None:
    choices = ("azure-kinect", "realsense", "all") if allow_all else (
        "azure-kinect",
        "realsense",
    )
    parser.add_argument(
        "--camera",
        type=_camera,
        choices=choices,
        default=default,
        help=(
            "相机后端；azure/k4a 与 d435/d435i/rs 可作为简写"
            f"（默认 {default}）"
        ),
    )


def _add_sensor_options(parser: argparse.ArgumentParser) -> None:
    _add_camera_option(parser, default="azure-kinect")
    parser.add_argument("--sensor", type=int, default=0, help="设备编号（默认 0）")
    parser.add_argument(
        "--sensor-config",
        type=_path,
        help=(
            "相机 JSON 配置；默认按后端选择 "
            f"{DEFAULT_AZURE_SENSOR_CONFIG} 或 {DEFAULT_REALSENSE_SENSOR_CONFIG}"
        ),
    )
    parser.add_argument(
        "--unaligned",
        action="store_true",
        help="预览时不把深度对齐到彩色图像，以降低实时计算量",
    )


def _add_reconstruction_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reconstruction-config",
        type=_path,
        default=DEFAULT_RECONSTRUCTION_CONFIG,
        help=f"重建 JSON 配置（默认 {DEFAULT_RECONSTRUCTION_CONFIG}）",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="覆盖一个高级参数，可重复使用；VALUE 支持 JSON 值",
    )
    parser.add_argument(
        "--stages",
        help="逗号分隔的阶段；默认 make,register,refine,integrate",
    )
    parser.add_argument("--single-thread", action="store_true", help="关闭 Python 多进程")
    parser.add_argument("--debug", action="store_true", help="开启 Open3D 调试可视化")
    parser.add_argument(
        "--compute-device",
        default="CPU:0",
        help="SLAC 计算设备，例如 CPU:0 或 CUDA:0",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open3d-reconstruct",
        description="本地隔离的 Azure Kinect / RealSense 采集与 Open3D 完整重建系统",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="检查本地环境与设备")
    doctor_parser.add_argument(
        "--require-device", action="store_true", help="把未发现设备视为失败"
    )
    _add_camera_option(doctor_parser, default="all", allow_all=True)

    list_parser = subparsers.add_parser("list", help="列出支持的 RGB-D 设备")
    _add_camera_option(list_parser, default="all", allow_all=True)

    preview_parser = subparsers.add_parser("preview", help="实时预览或无界面读帧测试")
    _add_sensor_options(preview_parser)
    preview_parser.add_argument("--no-window", action="store_true", help="不创建图形窗口")
    preview_parser.add_argument("--frames", type=int, help="读取指定帧数后退出")

    record_parser = subparsers.add_parser("record", help="录制 MKV 或 RealSense BAG")
    _add_sensor_options(record_parser)
    record_parser.add_argument(
        "--output", type=_path, help=".mkv/.bag 输出；默认保存到 data/recordings"
    )
    record_parser.add_argument("--name", help="使用默认输出目录时的录制名称")
    record_parser.add_argument("--seconds", type=float, help="指定录制秒数；省略则手动结束")
    record_parser.add_argument("--no-preview", action="store_true", help="不创建预览窗口")
    record_parser.add_argument("--force", action="store_true", help="覆盖已有录制文件")

    extract_parser = subparsers.add_parser(
        "extract", help="把 MKV/BAG 提取为对齐 RGB-D 帧"
    )
    extract_parser.add_argument("input", type=_path, help="输入 .mkv 或 .bag")
    extract_parser.add_argument("--output", type=_path, help="输出数据集目录")
    extract_parser.add_argument("--stride", type=int, default=1, help="每 N 帧保留一帧（默认 1）")
    extract_parser.add_argument("--force", action="store_true", help="重新生成本程序创建的数据集")

    reconstruct_parser = subparsers.add_parser(
        "reconstruct", help="从 MKV、BAG 或已提取 RGB-D 目录执行完整重建"
    )
    reconstruct_parser.add_argument(
        "input", type=_path, help="输入 .mkv、.bag 或 RGB-D 数据集目录"
    )
    reconstruct_parser.add_argument("--dataset", type=_path, help="录制文件的帧提取目录")
    reconstruct_parser.add_argument(
        "--force-extract", action="store_true", help="强制重新提取录制文件"
    )
    reconstruct_parser.add_argument(
        "--stride", type=int, default=1, help="提取时每 N 帧保留一帧"
    )
    _add_reconstruction_options(reconstruct_parser)

    scan_parser = subparsers.add_parser("scan", help="一次完成定时录制、提取和完整重建")
    _add_sensor_options(scan_parser)
    scan_parser.add_argument("--name", help="扫描名称；默认使用时间戳")
    scan_parser.add_argument("--seconds", type=float, default=30.0, help="录制秒数（默认 30）")
    scan_parser.add_argument("--no-preview", action="store_true", help="录制时不创建预览窗口")
    scan_parser.add_argument("--force", action="store_true", help="覆盖同名的已有录制/数据集")
    _add_reconstruction_options(scan_parser)

    color_parser = subparsers.add_parser("color-map", help="对已融合网格执行颜色映射优化")
    color_parser.add_argument("dataset", type=_path, help="已完成重建的数据集目录")
    color_parser.add_argument(
        "--reconstruction-config", type=_path, default=DEFAULT_RECONSTRUCTION_CONFIG
    )
    color_parser.add_argument("--set", dest="overrides", action="append", default=[])
    color_parser.add_argument("--sample-rate", type=int, default=10, help="关键帧采样间隔")
    color_parser.add_argument("--visualize", action="store_true", help="显示优化前后的窗口")

    web_parser = subparsers.add_parser(
        "web", help="启动本机单端口 Web 录制与重建控制台"
    )
    web_parser.add_argument(
        "--port", type=int, default=11920, help="本机监听端口（默认 11920）"
    )
    web_parser.add_argument(
        "--no-browser", action="store_true", help="启动后不自动打开浏览器"
    )

    service_parser = subparsers.add_parser(
        "service", help="管理 11920 端口的单例 Web 后台服务"
    )
    service_commands = service_parser.add_subparsers(
        dest="service_command", required=True
    )
    service_commands.add_parser("start", help="在后台启动唯一服务实例")
    service_status_parser = service_commands.add_parser(
        "status", help="检查进程身份与 HTTP 健康状态"
    )
    service_status_parser.add_argument(
        "--quiet", action="store_true", help="不输出状态文本，只返回状态码"
    )
    service_commands.add_parser("stop", help="安全停止服务及当前相机任务")

    udev_parser = subparsers.add_parser(
        "udev-install",
        help="在 Linux 安装设备权限规则（macOS 不使用 udev）",
    )
    _add_camera_option(udev_parser, default="all", allow_all=True)
    subparsers.add_parser("self-test", help="运行不需要设备的离线 RGB-D 重建自检")
    return parser


def _dataset_for_input(
    input_path: Path,
    destination: Path | None,
    *,
    force_extract: bool,
    stride: int,
) -> Path:
    input_path = input_path.expanduser().resolve()
    if input_path.is_file() and input_path.suffix.lower() in {".mkv", ".bag"}:
        target = (destination or (DATASETS_DIR / input_path.stem)).expanduser().resolve()
        return _extract_recording(
            input_path, target, force=force_extract, stride=stride
        )
    if destination is not None:
        raise ValueError("输入已经是数据集目录时不能再指定 --dataset")
    if not input_path.is_dir():
        raise ValueError(f"输入不是 MKV、BAG 或数据集目录: {input_path}")
    return input_path


def _backend(camera: str):
    module_name = "azure" if camera == "azure-kinect" else "realsense"
    return importlib.import_module(f"{__package__}.{module_name}")


def _extract_recording(
    source: Path, destination: Path, *, force: bool, stride: int
) -> Path:
    suffix = source.expanduser().suffix.lower()
    if suffix == ".mkv":
        from .azure import extract_mkv

        return extract_mkv(source, destination, force=force, stride=stride)
    if suffix == ".bag":
        from .realsense import extract_bag

        return extract_bag(source, destination, force=force, stride=stride)
    raise ValueError(f"无法识别录制格式（只支持 .mkv/.bag）: {source}")


def _run_reconstruction(args: argparse.Namespace, dataset: Path) -> Path | None:
    from .configuration import build_reconstruction_config
    from .pipeline import parse_stages, run_pipeline

    intrinsic = dataset / "intrinsic.json"
    config = build_reconstruction_config(
        dataset,
        intrinsic,
        args.reconstruction_config,
        args.overrides,
        debug=args.debug,
        single_thread=args.single_thread,
        device=args.compute_device,
    )
    return run_pipeline(config, parse_stages(args.stages))


def dispatch(args: argparse.Namespace) -> int:
    ensure_local_directories()
    if args.command == "doctor":
        from .doctor import run_doctor

        return run_doctor(require_device=args.require_device, camera=args.camera)
    if args.command == "list":
        cameras = ("azure-kinect", "realsense") if args.camera == "all" else (args.camera,)
        for index, camera in enumerate(cameras):
            if index:
                print()
            _backend(camera).list_devices()
        return 0
    if args.command == "preview":
        _backend(args.camera).preview(
            sensor_config=args.sensor_config,
            device=args.sensor,
            align_depth_to_color=not args.unaligned,
            no_window=args.no_window,
            frames=args.frames,
        )
        return 0
    if args.command == "record":
        backend = _backend(args.camera)
        output = args.output or backend.default_recording_path(args.name)
        backend.record(
            output,
            sensor_config=args.sensor_config,
            device=args.sensor,
            seconds=args.seconds,
            preview_window=not args.no_preview,
            align_depth_to_color=not args.unaligned,
            force=args.force,
        )
        return 0
    if args.command == "extract":
        destination = args.output or (DATASETS_DIR / args.input.stem)
        _extract_recording(
            args.input, destination, force=args.force, stride=args.stride
        )
        return 0
    if args.command == "reconstruct":
        dataset = _dataset_for_input(
            args.input,
            args.dataset,
            force_extract=args.force_extract,
            stride=args.stride,
        )
        _run_reconstruction(args, dataset)
        return 0
    if args.command == "scan":
        backend = _backend(args.camera)
        recording = backend.default_recording_path(args.name)
        dataset = DATASETS_DIR / recording.stem
        backend.record(
            recording,
            sensor_config=args.sensor_config,
            device=args.sensor,
            seconds=args.seconds,
            preview_window=not args.no_preview,
            align_depth_to_color=not args.unaligned,
            force=args.force,
        )
        dataset = _extract_recording(recording, dataset, force=args.force, stride=1)
        _run_reconstruction(args, dataset)
        return 0
    if args.command == "color-map":
        from .configuration import build_reconstruction_config
        from .pipeline import run_color_map

        dataset = args.dataset.expanduser().resolve()
        config = build_reconstruction_config(
            dataset,
            dataset / "intrinsic.json",
            args.reconstruction_config,
            args.overrides,
        )
        run_color_map(config, sample_rate=args.sample_rate, visualize=args.visualize)
        return 0
    if args.command == "web":
        from .web import serve_web

        return serve_web(port=args.port, open_browser=not args.no_browser)
    if args.command == "service":
        from .service import start_service, status_service, stop_service

        if args.service_command == "start":
            return start_service()
        if args.service_command == "status":
            return status_service(quiet=args.quiet)
        if args.service_command == "stop":
            return stop_service()
        raise AssertionError(f"未处理的服务命令: {args.service_command}")
    if args.command == "udev-install":
        from .permissions import install_udev_rules

        install_udev_rules(args.camera)
        return 0
    if args.command == "self-test":
        from .selftest import run_self_test

        run_self_test()
        return 0
    raise AssertionError(f"未处理的命令: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("\n操作已由用户中止。", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
