from __future__ import annotations

import ctypes
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .compute import resolve_compute_backend
from .paths import (
    AZURE_UDEV_RULE,
    IS_LINUX,
    IS_MACOS,
    K4A_CORE_LIBRARY,
    K4A_DEPTH_ENGINE_LIBRARY,
    K4A_LIVE_SUPPORTED,
    K4A_RECORD_LIBRARY,
    REALSENSE_UDEV_RULE,
    ROOT,
    SUPPORTED_PLATFORM,
)
from .realsense import device_model, is_supported_device_name


K4A_USB_PRODUCTS = {"097a", "097b", "097c", "097d", "097e"}
K4A_CAMERA_PRODUCTS = {"097c", "097d"}
REALSENSE_CAMERA_PRODUCTS = {"0b07": "D435", "0b3a": "D435i"}


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _find_usb_devices(
    *, vendor: str, products: set[str]
) -> list[dict[str, object]]:
    if IS_MACOS:
        return _find_macos_usb_devices(vendor=vendor, products=products)
    devices: list[dict[str, object]] = []
    sysfs = Path("/sys/bus/usb/devices")
    if not sysfs.is_dir():
        return devices
    for entry in sorted(sysfs.iterdir()):
        detected_vendor = _read(entry / "idVendor")
        product = _read(entry / "idProduct")
        if detected_vendor != vendor or product not in products:
            continue
        bus = _read(entry / "busnum")
        number = _read(entry / "devnum")
        speed_text = _read(entry / "speed")
        node = None
        if bus and number:
            node = Path("/dev/bus/usb") / f"{int(bus):03d}" / f"{int(number):03d}"
        try:
            speed = float(speed_text) if speed_text else None
        except ValueError:
            speed = None
        devices.append(
            {
                "product": product,
                "name": _read(entry / "product"),
                "node": node,
                "speed": speed,
                "readable": bool(node and os.access(node, os.R_OK)),
                "writable": bool(node and os.access(node, os.W_OK)),
            }
        )
    return devices


def _find_macos_usb_devices(
    *, vendor: str, products: set[str]
) -> list[dict[str, object]]:
    ioreg = shutil.which("ioreg")
    if ioreg is None:
        return []
    try:
        result = subprocess.run(
            [
                ioreg,
                "-p",
                "IOUSB",
                "-c",
                "IOUSBHostDevice",
                "-r",
                "-l",
                "-a",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        roots = plistlib.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException):
        return []

    expected_vendor = int(vendor, 16)
    expected_products = {int(item, 16) for item in products}
    devices: list[dict[str, object]] = []

    def visit(item: object) -> None:
        if not isinstance(item, dict):
            return
        if item.get("idVendor") == expected_vendor and item.get("idProduct") in expected_products:
            link_speed = item.get("UsbLinkSpeed")
            try:
                speed = float(link_speed) / 1_000_000 if link_speed else None
            except (TypeError, ValueError):
                speed = None
            devices.append(
                {
                    "product": f"{int(item['idProduct']):04x}",
                    "name": item.get("USB Product Name") or item.get("kUSBProductString"),
                    "node": None,
                    "speed": speed,
                    "readable": None,
                    "writable": None,
                }
            )
        children = item.get("IORegistryEntryChildren", [])
        if isinstance(children, list):
            for child in children:
                visit(child)

    if isinstance(roots, list):
        for root in roots:
            visit(root)
    return devices


def find_k4a_usb_devices() -> list[dict[str, object]]:
    return _find_usb_devices(vendor="045e", products=K4A_USB_PRODUCTS)


def find_realsense_usb_devices() -> list[dict[str, object]]:
    return _find_usb_devices(
        vendor="8086", products=set(REALSENSE_CAMERA_PRODUCTS)
    )


def _load_k4a() -> tuple[ctypes.CDLL | None, str | None]:
    if not K4A_LIVE_SUPPORTED:
        return (
            None,
            "Azure Kinect 官方 Sensor SDK 仅支持 Linux/Windows；macOS 仅支持离线 MKV",
        )
    library = K4A_CORE_LIBRARY
    try:
        return ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL), None
    except OSError as exc:
        return None, str(exc)


def _installed_device_count(library: ctypes.CDLL) -> int:
    function = library.k4a_device_get_installed_count
    function.argtypes = []
    function.restype = ctypes.c_uint32
    return int(function())


def k4a_device_count() -> int:
    if not K4A_LIVE_SUPPORTED:
        raise RuntimeError(
            "Azure Kinect 实时采集在 macOS 上不可用；"
            "已有 MKV 可继续离线提取和重建"
        )
    library, error = _load_k4a()
    if library is None:
        raise RuntimeError(f"K4A 动态加载失败: {error}")
    return _installed_device_count(library)


def _base_checks() -> tuple[list[Check], object | None]:
    checks: list[Check] = []
    checks.append(
        Check(
            "ok" if SUPPORTED_PLATFORM else "fail",
            "平台",
            f"{platform.system()} {platform.machine()}",
        )
    )

    python_local = _inside(Path(sys.prefix), ROOT / ".venv") and _inside(
        Path(sys.base_prefix), ROOT / ".python"
    )
    version_ok = sys.version_info[:2] == (3, 12)
    checks.append(
        Check(
            "ok" if python_local and version_ok else "fail",
            "Python",
            f"{platform.python_version()}，venv={sys.prefix}，runtime={sys.base_prefix}",
        )
    )
    external_python_paths = []
    for item in sys.path:
        if not item:
            continue
        path = Path(item)
        if path.exists() and not _inside(path, ROOT):
            external_python_paths.append(str(path.resolve()))
    checks.append(
        Check(
            "ok" if not external_python_paths else "fail",
            "Python 路径隔离",
            "所有模块搜索路径均在项目内"
            if not external_python_paths
            else "发现项目外路径: " + ", ".join(external_python_paths),
        )
    )

    try:
        import open3d as o3d

        module_path = Path(o3d.__file__).resolve()
        open3d_ok = o3d.__version__ == "0.19.0" and _inside(
            module_path, ROOT / ".venv"
        )
        checks.append(
            Check(
                "ok" if open3d_ok else "fail",
                "Open3D",
                f"{o3d.__version__} @ {module_path}",
            )
        )
        compute = resolve_compute_backend("auto")
        checks.append(
            Check(
                "ok" if compute.accelerated else "warn",
                "重建计算后端",
                compute.detail,
            )
        )
        return checks, o3d
    except Exception as exc:
        checks.append(Check("fail", "Open3D", f"导入失败: {exc}"))
        return checks, None


def _rule_check(label: str, source: Path, target: Path) -> Check:
    try:
        matches = target.read_bytes() == source.read_bytes()
    except OSError:
        matches = False
    return Check(
        "ok" if matches else "warn",
        f"[{label}] udev 规则",
        "已安装"
        if matches
        else f"尚未安装；设备权限不足时运行 udev-install --camera {label.lower()}",
    )


def _usb_camera_checks(
    devices: list[dict[str, object]],
    *,
    label: str,
    missing_text: str,
    require_device: bool,
) -> list[Check]:
    if not devices:
        if require_device:
            missing_text = missing_text.replace("（当前未接入时属正常）", "")
        return [
            Check(
                "fail" if require_device else "warn",
                f"[{label}] USB 设备",
                missing_text,
            )
        ]
    descriptions: list[str] = []
    permission_ok = True
    speed_ok = True
    for item in devices:
        node = item["node"]
        speed = item["speed"]
        name = item.get("name") or item["product"]
        if IS_LINUX:
            permission_ok = permission_ok and bool(
                item["readable"] and item["writable"]
            )
        speed_ok = speed_ok and bool(speed is None or float(speed) >= 5000)
        location = str(node) if node is not None else "macOS IORegistry"
        descriptions.append(f"{name} @ {location}，{speed or '?'} Mb/s")
    checks = [
        Check("ok", f"[{label}] USB 设备", "; ".join(descriptions)),
        Check(
            "ok" if speed_ok else "warn",
            f"[{label}] USB 链路",
            "USB 3.x" if speed_ok else "链路低于 5 Gb/s，采集可能不稳定",
        ),
    ]
    if IS_LINUX:
        checks.insert(
            1,
            Check(
                "ok" if permission_ok else "fail",
                f"[{label}] USB 权限",
                "可读写"
                if permission_ok
                else (
                    f"权限不足；运行 udev-install --camera {label.lower()} 后重新插拔"
                ),
            ),
        )
    return checks


def _azure_checks(o3d: object | None, *, require_device: bool) -> tuple[list[Check], int]:
    checks: list[Check] = []
    azure_api = bool(
        o3d
        and bool(getattr(o3d, "_build_config", {}).get("BUILD_AZURE_KINECT"))
        and all(
            hasattr(o3d.io, name)
            for name in (
                "AzureKinectSensor",
                "AzureKinectRecorder",
                "AzureKinectMKVReader",
            )
        )
    )
    if not K4A_LIVE_SUPPORTED:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        portable_ready = bool(ffmpeg and ffprobe)
        checks.append(
            Check(
                "ok" if portable_ready else "fail",
                "[Azure] MKV 离线",
                "FFmpeg 标定提取后端可用"
                if portable_ready
                else "缺少 ffmpeg/ffprobe；请运行 brew install ffmpeg",
            )
        )
        checks.append(
            Check(
                "fail" if require_device else "warn",
                "[Azure] 实时采集",
                "Azure Kinect Sensor SDK 不支持 macOS；已有 MKV 可正常重建",
            )
        )
        cameras = find_k4a_usb_devices()
        if cameras:
            checks.append(
                Check(
                    "warn",
                    "[Azure] USB 设备",
                    "macOS 已检测到 Azure Kinect，但官方 SDK 无法在本平台采集",
                )
            )
        return checks, 0

    checks.append(
        Check(
            "ok" if azure_api else "fail",
            "[Azure] Open3D API",
            "接口完整" if azure_api else "接口缺失",
        )
    )

    expected_libraries = (
        K4A_CORE_LIBRARY,
        K4A_RECORD_LIBRARY,
        K4A_DEPTH_ENGINE_LIBRARY,
    )
    missing = [str(path) for path in expected_libraries if not path.is_file()]
    checks.append(
        Check(
            "ok" if not missing else "fail",
            "[Azure] 本地运行库",
            "K4A 1.4.1（项目内）" if not missing else "缺少: " + ", ".join(missing),
        )
    )

    library, load_error = _load_k4a()
    checks.append(
        Check(
            "ok" if library else "fail",
            "[Azure] K4A 加载",
            "成功" if library else f"失败: {load_error}",
        )
    )
    auxiliary_errors: list[str] = []
    if library:
        for label, path in (
            ("record", K4A_RECORD_LIBRARY),
            ("depth", K4A_DEPTH_ENGINE_LIBRARY),
        ):
            try:
                ctypes.CDLL(str(path))
            except OSError as exc:
                auxiliary_errors.append(f"{label}: {exc}")
    checks.append(
        Check(
            "ok" if library and not auxiliary_errors else "fail",
            "[Azure] 录制/深度引擎",
            "加载成功"
            if library and not auxiliary_errors
            else ("; ".join(auxiliary_errors) or f"K4A 未加载: {load_error}"),
        )
    )

    usb_devices = find_k4a_usb_devices()
    cameras = [item for item in usb_devices if item["product"] in K4A_CAMERA_PRODUCTS]
    checks.extend(
        _usb_camera_checks(
            cameras,
            label="Azure",
            missing_text="未发现 Azure Kinect（当前未接入时属正常）",
            require_device=require_device,
        )
    )

    count = 0
    if library:
        try:
            count = _installed_device_count(library)
            checks.append(
                Check(
                    "ok" if count > 0 else ("fail" if require_device else "warn"),
                    "[Azure] SDK 枚举",
                    f"发现 {count} 台设备" if count else "SDK 当前枚举到 0 台设备",
                )
            )
        except Exception as exc:
            checks.append(Check("fail", "[Azure] SDK 枚举", str(exc)))
    checks.append(
        _rule_check(
            "Azure", AZURE_UDEV_RULE, Path("/etc/udev/rules.d/99-k4a.rules")
        )
    )
    return checks, count


def _realsense_checks(
    o3d: object | None, *, require_device: bool
) -> tuple[list[Check], int]:
    checks: list[Check] = []
    realsense_api = bool(
        o3d
        and bool(o3d._build_config.get("BUILD_LIBREALSENSE"))
        and all(
            hasattr(o3d.t.io, name)
            for name in ("RealSenseSensor", "RealSenseSensorConfig", "RSBagReader")
        )
    )
    checks.append(
        Check(
            "ok" if realsense_api else "fail",
            "[RealSense] Open3D API",
            "BUILD_LIBREALSENSE=True，接口完整"
            if realsense_api
            else "librealsense 构建或接口缺失",
        )
    )
    checks.append(
        Check(
            "ok" if realsense_api else "fail",
            "[RealSense] 本地运行库",
            "librealsense 已静态集成在项目 .venv 的 Open3D wheel 中"
            if realsense_api
            else "不可用",
        )
    )

    usb_devices = find_realsense_usb_devices()
    checks.extend(
        _usb_camera_checks(
            usb_devices,
            label="RealSense",
            missing_text="未发现 D435/D435i（当前未接入时属正常）",
            require_device=require_device,
        )
    )

    supported: list[object] = []
    all_devices: list[object] = []
    if realsense_api:
        try:
            all_devices = list(o3d.t.io.RealSenseSensor.enumerate_devices())
            supported = [
                item for item in all_devices if is_supported_device_name(item.name)
            ]
            if supported:
                detail = "; ".join(
                    f"{device_model(item.name)} {item.serial}" for item in supported
                )
                checks.append(Check("ok", "[RealSense] SDK 枚举", detail))
            else:
                extra = (
                    "；另发现: " + ", ".join(item.name for item in all_devices)
                    if all_devices
                    else ""
                )
                checks.append(
                    Check(
                        "fail" if require_device else "warn",
                        "[RealSense] SDK 枚举",
                        "librealsense 当前枚举到 0 台 D435/D435i" + extra,
                    )
                )
        except Exception as exc:
            checks.append(
                Check(
                    "fail" if require_device else "warn",
                    "[RealSense] SDK 枚举",
                    str(exc),
                )
            )

    checks.append(
        Check(
            "ok",
            "[RealSense] 重建通道",
            "D435/D435i 使用同步 RGB-D；D435i IMU 不参与 Open3D 经典重建",
        )
    )
    if IS_LINUX:
        checks.append(
            _rule_check(
                "RealSense",
                REALSENSE_UDEV_RULE,
                Path("/etc/udev/rules.d/99-realsense-libusb.rules"),
            )
        )
    else:
        checks.append(
            Check(
                "warn",
                "[RealSense] macOS USB",
                "macOS 12+ 可能要求以 sudo 运行实时采集；BAG 离线重建不受影响",
            )
        )
    return checks, len(supported)


def collect_checks(
    require_device: bool = False, *, camera: str = "all"
) -> list[Check]:
    if camera not in {"azure-kinect", "realsense", "all"}:
        raise ValueError(f"未知相机后端: {camera}")
    checks, o3d = _base_checks()
    selected = (
        ("azure-kinect", "realsense") if camera == "all" else (camera,)
    )
    counts: list[int] = []
    backend_requires_device = require_device and camera != "all"
    if "azure-kinect" in selected:
        backend_checks, count = _azure_checks(
            o3d, require_device=backend_requires_device
        )
        checks.extend(backend_checks)
        counts.append(count)
    if "realsense" in selected:
        backend_checks, count = _realsense_checks(
            o3d, require_device=backend_requires_device
        )
        checks.extend(backend_checks)
        counts.append(count)
    if require_device and camera == "all":
        total = sum(counts)
        checks.append(
            Check(
                "ok" if total else "fail",
                "设备要求",
                f"发现 {total} 台受支持设备" if total else "未发现任何受支持设备",
            )
        )
    return checks


def print_checks(checks: Iterable[Check]) -> None:
    symbols = {"ok": "✓", "warn": "!", "fail": "✗"}
    for check in checks:
        print(f"[{symbols[check.level]}] {check.name:<24} {check.detail}")


def run_doctor(require_device: bool = False, *, camera: str = "all") -> int:
    checks = collect_checks(require_device=require_device, camera=camera)
    print_checks(checks)
    failures = [check for check in checks if check.level == "fail"]
    if failures:
        print(f"\n诊断未通过：{len(failures)} 项失败。")
        return 1
    if require_device:
        print("\n本地环境和设备诊断通过。")
    else:
        print(
            "\n本地环境可用；设备接入后请运行 "
            f"doctor --camera {camera} --require-device。"
        )
    return 0
