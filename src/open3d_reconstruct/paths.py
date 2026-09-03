from __future__ import annotations

import os
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
K4A_LIB_DIR = ROOT / ".deps" / "k4a" / "usr" / "lib" / "x86_64-linux-gnu"
VENDOR_RECONSTRUCTION_DIR = (
    Path(__file__).resolve().parent / "vendor" / "reconstruction_system"
)


def ensure_local_directories() -> None:
    for path in (RECORDINGS_DIR, DATASETS_DIR, ROOT / ".cache", RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)
