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
DEFAULT_RECONSTRUCTION_PROFILES = ROOT / "config" / "reconstruction.yaml"
AZURE_UDEV_RULE = ROOT / "config" / "99-k4a.rules"
REALSENSE_UDEV_RULE = ROOT / "config" / "99-realsense-libusb.rules"
# Backwards-compatible name used by older callers.
DEFAULT_UDEV_RULE = AZURE_UDEV_RULE
RECORDINGS_DIR = ROOT / "data" / "recordings"
DATASETS_DIR = ROOT / "data" / "datasets"
SYSTEM = platform.system()
MACHINE = platform.machine().lower()
IS_LINUX = SYSTEM == "Linux"
IS_MACOS = SYSTEM == "Darwin"
IS_WINDOWS = SYSTEM == "Windows"
IS_WSL = IS_LINUX and (
    "microsoft" in platform.release().lower()
    or bool(os.environ.get("WSL_INTEROP"))
    or bool(os.environ.get("WSL_DISTRO_NAME"))
)
SUPPORTED_PLATFORM = (IS_LINUX and MACHINE == "x86_64") or (
    IS_MACOS and MACHINE in {"arm64", "x86_64"}
) or (
    IS_WINDOWS and MACHINE in {"amd64", "x86_64"}
)
VENV_DIR = ROOT / (".venv-windows" if IS_WINDOWS else ".venv")
PYTHON_RUNTIME_DIR = ROOT / (".python-windows" if IS_WINDOWS else ".python")
RUNTIME_DIR = ROOT / ".run" / "windows" if IS_WINDOWS else ROOT / ".run"
K4A_LIVE_SUPPORTED = (
    (IS_LINUX and MACHINE == "x86_64")
    or (IS_WINDOWS and MACHINE in {"amd64", "x86_64"})
)
if IS_LINUX:
    K4A_LIB_DIR = ROOT / ".deps" / "k4a" / "usr" / "lib" / "x86_64-linux-gnu"
    K4A_CORE_LIBRARY = K4A_LIB_DIR / "libk4a.so.1.4"
    K4A_RECORD_LIBRARY = K4A_LIB_DIR / "libk4arecord.so.1.4"
    K4A_DEPTH_ENGINE_LIBRARY = K4A_LIB_DIR / "libk4a1.4" / "libdepthengine.so.2.0"
elif IS_WINDOWS:
    K4A_LIB_DIR = (
        ROOT
        / ".deps"
        / "k4a-windows"
        / "lib"
        / "native"
        / "amd64"
        / "release"
    )
    K4A_CORE_LIBRARY = K4A_LIB_DIR / "k4a.dll"
    K4A_RECORD_LIBRARY = K4A_LIB_DIR / "k4arecord.dll"
    K4A_DEPTH_ENGINE_LIBRARY = K4A_LIB_DIR / "depthengine_2_0.dll"
else:
    K4A_LIB_DIR = ROOT / ".deps" / "k4a" / "lib"
    K4A_CORE_LIBRARY = K4A_LIB_DIR / "libk4a.dylib"
    K4A_RECORD_LIBRARY = K4A_LIB_DIR / "libk4arecord.dylib"
    K4A_DEPTH_ENGINE_LIBRARY = K4A_LIB_DIR / "libdepthengine.dylib"
VENDOR_RECONSTRUCTION_DIR = (
    Path(__file__).resolve().parent / "vendor" / "reconstruction_system"
)


def ensure_local_directories() -> None:
    for path in (RECORDINGS_DIR, DATASETS_DIR, ROOT / ".cache", RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)
