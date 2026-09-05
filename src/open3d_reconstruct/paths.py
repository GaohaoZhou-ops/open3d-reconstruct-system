from __future__ import annotations

import os
import platform
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("OPEN3D_RECONSTRUCT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
DEFAULT_AZURE_SENSOR_CONFIG = ROOT / "config" / "azure-kinect.json"
DEFAULT_REALSENSE_SENSOR_CONFIG = ROOT / "config" / "realsense-d435.json"
# Backwards-compatible name used by the Azure Kinect backend.
DEFAULT_SENSOR_CONFIG = DEFAULT_AZURE_SENSOR_CONFIG
DEFAULT_RECONSTRUCTION_CONFIG = ROOT / "config" / "reconstruction.json"
AZURE_UDEV_RULE = ROOT / "config" / "99-k4a.rules"
REALSENSE_UDEV_RULE = ROOT / "config" / "99-realsense-libusb.rules"
# Backwards-compatible name used by older callers.
DEFAULT_UDEV_RULE = AZURE_UDEV_RULE
RECORDINGS_DIR = ROOT / "data" / "recordings"
DATASETS_DIR = ROOT / "data" / "datasets"
RUNTIME_DIR = ROOT / ".run"
SYSTEM = platform.system()
MACHINE = platform.machine().lower()
IS_LINUX = SYSTEM == "Linux"
IS_MACOS = SYSTEM == "Darwin"
SUPPORTED_PLATFORM = (IS_LINUX and MACHINE == "x86_64") or (
    IS_MACOS and MACHINE in {"arm64", "x86_64"}
)
K4A_LIVE_SUPPORTED = IS_LINUX and MACHINE == "x86_64"
K4A_LIB_DIR = (
    ROOT / ".deps" / "k4a" / "usr" / "lib" / "x86_64-linux-gnu"
    if K4A_LIVE_SUPPORTED
    else ROOT / ".deps" / "k4a" / "lib"
)
K4A_CORE_LIBRARY = K4A_LIB_DIR / (
    "libk4a.so.1.4" if K4A_LIVE_SUPPORTED else "libk4a.dylib"
)
K4A_RECORD_LIBRARY = K4A_LIB_DIR / (
    "libk4arecord.so.1.4" if K4A_LIVE_SUPPORTED else "libk4arecord.dylib"
)
K4A_DEPTH_ENGINE_LIBRARY = (
    K4A_LIB_DIR / "libk4a1.4" / "libdepthengine.so.2.0"
    if K4A_LIVE_SUPPORTED
    else K4A_LIB_DIR / "libdepthengine.dylib"
)
VENDOR_RECONSTRUCTION_DIR = (
    Path(__file__).resolve().parent / "vendor" / "reconstruction_system"
)


def ensure_local_directories() -> None:
    for path in (RECORDINGS_DIR, DATASETS_DIR, ROOT / ".cache", RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)
