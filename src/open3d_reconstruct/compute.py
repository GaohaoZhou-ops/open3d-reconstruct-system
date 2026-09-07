from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass
from typing import Any


COMPUTE_BACKENDS = ("auto", "cpu", "cuda", "mps")
_BACKEND_ALIASES = {
    "auto": "auto",
    "cpu": "cpu",
    "cpu:0": "cpu",
    "cuda": "cuda",
    "cuda:0": "cuda",
    "gpu": "auto",
    "metal": "mps",
    "mps": "mps",
    "mps:0": "mps",
}


@dataclass(frozen=True)
class ComputeSelection:
    requested: str
    backend: str
    device: str
    accelerated: bool
    detail: str
    torch_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_compute_backend(value: object) -> str:
    normalized = str(value or "auto").strip().lower()
    try:
        return _BACKEND_ALIASES[normalized]
    except KeyError as exc:
        choices = ", ".join(COMPUTE_BACKENDS)
        raise ValueError(f"compute_backend 必须是以下值之一: {choices}") from exc


def _import_torch() -> tuple[Any | None, str | None]:
    try:
        import torch

        return torch, None
    except Exception as exc:
        return None, f"PyTorch 导入失败: {exc}"


def _safe_bool(callable_value: Any) -> bool:
    try:
        return bool(callable_value())
    except Exception:
        return False


def probe_compute_backends(
    *, torch_module: Any | None = None, platform_name: str | None = None
) -> dict[str, Any]:
    current_platform = platform_name or sys.platform
    import_error: str | None = None
    torch = torch_module
    if torch is None:
        torch, import_error = _import_torch()

    result: dict[str, Any] = {
        "platform": current_platform,
        "machine": platform.machine(),
        "torch_available": torch is not None,
        "torch_version": (
            str(getattr(torch, "__version__", "")) or None
            if torch is not None
            else None
        ),
        "import_error": import_error,
        "cuda": {
            "available": False,
            "detail": "不可用",
            "compiled_version": None,
            "device_count": 0,
            "devices": [],
        },
        "mps": {"available": False, "detail": "不可用"},
    }
    if torch is None:
        return result

    cuda_available = _safe_bool(torch.cuda.is_available)
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    try:
        cuda_count = int(torch.cuda.device_count()) if cuda_available else 0
    except Exception:
        cuda_count = 1 if cuda_available else 0
    cuda_devices: list[str] = []
    for index in range(cuda_count):
        try:
            cuda_devices.append(str(torch.cuda.get_device_name(index)))
        except Exception:
            cuda_devices.append(f"CUDA:{index}")
    cuda_detail = "未检测到 CUDA GPU"
    if cuda_available:
        try:
            index = int(torch.cuda.current_device())
            cuda_detail = str(torch.cuda.get_device_name(index))
        except Exception:
            cuda_detail = "CUDA GPU"
    elif cuda_version:
        cuda_detail = f"PyTorch 包含 CUDA {cuda_version}，但容器当前看不到 GPU"
    result["cuda"] = {
        "available": cuda_available,
        "detail": cuda_detail,
        "compiled_version": str(cuda_version) if cuda_version else None,
        "device_count": cuda_count,
        "devices": cuda_devices,
    }

    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    mps_built = bool(mps_backend and _safe_bool(mps_backend.is_built))
    mps_available = bool(mps_backend and _safe_bool(mps_backend.is_available))
    if mps_available:
        mps_detail = f"Apple Metal GPU ({platform.machine()})"
    elif not mps_built:
        mps_detail = "PyTorch 未包含 MPS 支持"
    elif current_platform != "darwin":
        mps_detail = "MPS 仅适用于 macOS"
    else:
        mps_detail = "当前 macOS 或硬件不满足 MPS 运行条件"
    result["mps"] = {
        "available": mps_available,
        "built": mps_built,
        "detail": mps_detail,
    }
    return result


def resolve_compute_backend(
    requested: object = "auto",
    *,
    torch_module: Any | None = None,
    platform_name: str | None = None,
) -> ComputeSelection:
    normalized = normalize_compute_backend(requested)
    current_platform = platform_name or sys.platform
    probe = probe_compute_backends(
        torch_module=torch_module, platform_name=current_platform
    )
    torch_version = probe["torch_version"]

    if normalized == "cpu":
        return ComputeSelection(
            requested=normalized,
            backend="cpu",
            device="cpu",
            accelerated=False,
            detail="已按配置使用 Open3D CPU 后端",
            torch_version=torch_version,
        )

    preferred = normalized
    if normalized == "auto":
        preferred = "mps" if current_platform == "darwin" else "cuda"

    candidate = probe[preferred]
    if bool(candidate["available"]):
        label = "Metal/MPS" if preferred == "mps" else "CUDA"
        return ComputeSelection(
            requested=normalized,
            backend=preferred,
            device=preferred,
            accelerated=True,
            detail=f"{label} 已启用：{candidate['detail']}",
            torch_version=torch_version,
        )

    reason = candidate["detail"]
    if not probe["torch_available"]:
        reason = probe["import_error"] or "未安装 PyTorch"
    label = "Metal/MPS" if preferred == "mps" else "CUDA"
    return ComputeSelection(
        requested=normalized,
        backend="cpu",
        device="cpu",
        accelerated=False,
        detail=f"{label} 不可用（{reason}），已自动回退到 Open3D CPU 后端",
        torch_version=torch_version,
    )


def configure_compute_backend(config: dict[str, Any]) -> ComputeSelection:
    selection = resolve_compute_backend(config.get("compute_backend", "auto"))
    config["compute_backend"] = selection.requested
    config["compute_backend_resolved"] = selection.backend
    config["compute_backend_device"] = selection.device
    config["compute_accelerated"] = selection.accelerated
    config["compute_backend_detail"] = selection.detail
    return selection


def compute_readiness_report(requested: object = "auto") -> dict[str, Any]:
    """Return stable, serializable diagnostics for cloud GPU attachment checks."""

    probe = probe_compute_backends()
    selection = resolve_compute_backend(requested)
    return {
        "probe": probe,
        "selection": selection.to_dict(),
        "accelerated_stages": ["RGB-D 连续帧里程计"] if selection.accelerated else [],
        "cpu_stages": ["闭环初始化", "全局配准", "精细 ICP", "经典 TSDF 融合"],
    }


def print_compute_readiness(requested: object = "auto") -> ComputeSelection:
    report = compute_readiness_report(requested)
    probe = report["probe"]
    selection = ComputeSelection(**report["selection"])
    cuda = probe["cuda"]
    mps = probe["mps"]
    print(f"平台: {probe['platform']} / {probe['machine']}")
    print(
        "PyTorch: "
        + (str(probe["torch_version"]) if probe["torch_available"] else "不可用")
    )
    print(
        f"CUDA: {'可用' if cuda['available'] else '不可用'}；{cuda['detail']}"
    )
    if cuda.get("compiled_version"):
        print(f"CUDA 编译版本: {cuda['compiled_version']}")
    if cuda.get("devices"):
        print("GPU: " + "，".join(cuda["devices"]))
    print(f"MPS: {'可用' if mps['available'] else '不可用'}；{mps['detail']}")
    print(f"自动选择: {selection.backend.upper()}；{selection.detail}")
    print("GPU 加速范围: RGB-D 连续帧里程计；其余经典 Open3D 阶段保留 CPU 路径。")
    return selection
