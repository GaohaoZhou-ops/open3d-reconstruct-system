from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .paths import AZURE_UDEV_RULE, REALSENSE_UDEV_RULE


RULES = {
    "azure-kinect": (
        "Azure Kinect",
        AZURE_UDEV_RULE,
        Path("/etc/udev/rules.d/99-k4a.rules"),
    ),
    "realsense": (
        "RealSense D435/D435i",
        REALSENSE_UDEV_RULE,
        Path("/etc/udev/rules.d/99-realsense-libusb.rules"),
    ),
}


def _selected_rules(camera: str):
    if camera == "all":
        return list(RULES.values())
    try:
        return [RULES[camera]]
    except KeyError as exc:
        raise ValueError(f"未知相机后端: {camera}") from exc


def install_udev_rules(camera: str = "all") -> None:
    selected = _selected_rules(camera)
    pending: list[tuple[str, Path, Path]] = []
    for label, source, target in selected:
        if not source.is_file():
            raise RuntimeError(f"项目内规则文件缺失: {source}")
        try:
            current = target.read_bytes() == source.read_bytes()
        except OSError:
            current = False
        if current:
            print(f"{label} udev 规则已经是最新版本。")
        else:
            pending.append((label, source, target))
    if not pending:
        return

    sudo = shutil.which("sudo")
    if not sudo:
        raise RuntimeError("系统中找不到 sudo，无法安装 udev 权限规则")
    print("将项目内规则复制到 /etc/udev/rules.d/；sudo 可能要求输入密码。")
    try:
        for label, source, target in pending:
            subprocess.run(
                [sudo, "install", "-m", "0644", str(source), str(target)],
                check=True,
            )
            print(f"已安装 {label}: {target}")
        subprocess.run([sudo, "udevadm", "control", "--reload-rules"], check=True)
        subprocess.run(
            [sudo, "udevadm", "trigger", "--subsystem-match=usb"], check=True
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"udev 规则安装失败（退出码 {exc.returncode}）") from exc
    labels = "、".join(label for label, _source, _target in pending)
    print(f"udev 规则已安装（{labels}）。请重新插拔对应设备。")


def install_udev_rule() -> None:
    """Backwards-compatible Azure-only entry point."""

    install_udev_rules("azure-kinect")
