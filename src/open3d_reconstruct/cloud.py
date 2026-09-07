from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, TextIO

from .compute import resolve_compute_backend
from .configuration import (
    ReconstructionPlan,
    load_reconstruction_plan,
    reconstruction_profile_catalog,
)
from .paths import (
    DATASETS_DIR,
    DEFAULT_RECONSTRUCTION_PROFILES,
    RECORDINGS_DIR,
    ROOT,
    ensure_local_directories,
)


InputFunction = Callable[[str], str]
CommandRunner = Callable[[list[str]], int]


def _is_rgbd_dataset(path: Path) -> bool:
    if not path.is_dir() or not (path / "intrinsic.json").is_file():
        return False
    color_exists = any((path / name).is_dir() for name in ("color", "image", "rgb"))
    return color_exists and (path / "depth").is_dir()


def discover_reconstruction_inputs() -> list[Path]:
    """Find recordings and already extracted datasets without a desktop picker."""

    candidates: list[Path] = []
    # `data/recording` appeared in early deployments.  Continue discovering it
    # while keeping `data/recordings` as the canonical managed directory.
    recording_roots = (RECORDINGS_DIR, ROOT / "data" / "recording")
    for directory in recording_roots:
        if not directory.is_dir():
            continue
        for suffix in ("*.mkv", "*.MKV", "*.bag", "*.BAG"):
            candidates.extend(path for path in directory.glob(suffix) if path.is_file())
    if DATASETS_DIR.is_dir():
        candidates.extend(path for path in DATASETS_DIR.iterdir() if _is_rgbd_dataset(path))

    unique: dict[Path, Path] = {}
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            key = path.absolute()
        unique.setdefault(key, path.absolute())
    return sorted(
        unique.values(),
        key=lambda item: (
            -(item.stat().st_mtime_ns if item.exists() else 0),
            str(item).lower(),
        ),
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except (OSError, ValueError):
        return str(path)


def _size_label(path: Path) -> str:
    if path.is_dir():
        return "RGB-D 数据集"
    try:
        size = path.stat().st_size
    except OSError:
        return "录制文件"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    return f"{value:.1f} {unit}"


def _ask(
    prompt: str,
    *,
    input_fn: InputFunction,
    output: TextIO,
) -> str:
    output.write(prompt)
    output.flush()
    try:
        return input_fn("").strip()
    except EOFError as exc:
        raise RuntimeError("交互输入已结束，未启动重建") from exc


def _choose_number(
    prompt: str,
    count: int,
    *,
    input_fn: InputFunction,
    output: TextIO,
    default: int | None = None,
) -> int:
    while True:
        suffix = f"（默认 {default}）" if default is not None else ""
        raw = _ask(f"{prompt}{suffix}: ", input_fn=input_fn, output=output)
        if not raw and default is not None:
            return default
        try:
            selected = int(raw)
        except ValueError:
            selected = 0
        if 1 <= selected <= count:
            return selected
        output.write(f"请输入 1 到 {count} 之间的编号。\n")


def _validate_input(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_file() and resolved.suffix.lower() in {".mkv", ".bag"}:
        return resolved
    if _is_rgbd_dataset(resolved):
        return resolved
    raise ValueError(f"输入必须是 MKV、BAG 或含内参与 RGB-D 帧的数据集: {resolved}")


def _choose_input(
    supplied: Path | None,
    *,
    input_fn: InputFunction,
    output: TextIO,
) -> Path:
    if supplied is not None:
        return _validate_input(supplied)
    candidates = discover_reconstruction_inputs()
    output.write("\n[1/4] 选择输入\n")
    for index, path in enumerate(candidates, start=1):
        output.write(f"  {index}. {_display_path(path)}（{_size_label(path)}）\n")
    manual_index = len(candidates) + 1
    output.write(f"  {manual_index}. 手动输入路径\n")
    selected = _choose_number(
        "请选择",
        manual_index,
        input_fn=input_fn,
        output=output,
        default=1 if candidates else manual_index,
    )
    if selected <= len(candidates):
        return _validate_input(candidates[selected - 1])
    raw = _ask("请输入 MKV、BAG 或数据集路径: ", input_fn=input_fn, output=output)
    if not raw:
        raise RuntimeError("没有输入文件路径，未启动重建")
    return _validate_input(Path(raw))


def _choose_profile(
    config_path: Path,
    supplied: str | None,
    *,
    input_fn: InputFunction,
    output: TextIO,
) -> ReconstructionPlan:
    catalog = reconstruction_profile_catalog(config_path)
    profiles = catalog["profiles"]
    if supplied is not None:
        return load_reconstruction_plan(config_path, supplied)

    output.write("\n[2/4] 选择重建质量\n")
    names = list(profiles)
    default_name = str(catalog["default_profile"])
    default_index = names.index(default_name) + 1
    for index, name in enumerate(names, start=1):
        item = profiles[name]
        marker = "（默认）" if name == default_name else ""
        output.write(
            f"  {index}. {item['label']} [{name}] {marker}\n"
            f"     {item['description']}\n"
        )
    selected = _choose_number(
        "请选择",
        len(names),
        input_fn=input_fn,
        output=output,
        default=default_index,
    )
    return load_reconstruction_plan(config_path, names[selected - 1])


def dataset_target(source: Path, plan: ReconstructionPlan) -> Path | None:
    if source.is_dir():
        return None
    profile_suffix = plan.profile or plan.source.stem
    compact = "-".join(profile_suffix.strip().split())
    safe_suffix = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in compact
    ).strip(".-") or "custom"
    return DATASETS_DIR / f"{source.stem}-{safe_suffix}"


def reconstruction_arguments(
    source: Path,
    plan: ReconstructionPlan,
    target: Path | None,
) -> list[str]:
    arguments = [
        "reconstruct",
        str(source),
        "--reconstruction-config",
        str(plan.source),
    ]
    if plan.profile is not None:
        arguments.extend(("--profile", plan.profile))
    if target is not None:
        arguments.extend(("--dataset", str(target)))
        if target.exists():
            from .extraction import existing_extraction_matches

            if not existing_extraction_matches(source, target, stride=plan.stride):
                arguments.append("--force-extract")
    return arguments


def run_cloud_wizard(
    *,
    source: Path | None = None,
    config_path: Path = DEFAULT_RECONSTRUCTION_PROFILES,
    profile: str | None = None,
    assume_yes: bool = False,
    input_fn: InputFunction | None = None,
    output: TextIO | None = None,
    runner: CommandRunner | None = None,
) -> int:
    """Run the deliberately small, headless reconstruction assistant."""

    ensure_local_directories()
    selected_input = input_fn or input
    selected_output = output or sys.stdout
    selected_config = config_path.expanduser().resolve()

    selected_output.write("Open3D 云端重建向导\n")
    selected_output.write("无需桌面环境；输入与结果始终保留在当前工程中。\n")
    source_path = _choose_input(
        source, input_fn=selected_input, output=selected_output
    )
    plan = _choose_profile(
        selected_config,
        profile,
        input_fn=selected_input,
        output=selected_output,
    )

    selected_output.write("\n[3/4] 检测计算资源\n")
    # Keep the detailed report on the real stdout during normal CLI use.  Tests
    # can pass a separate stream and still assert the stable summary below.
    compute = resolve_compute_backend(plan.compute_backend)
    selected_output.write(f"  {compute.detail}\n")
    target = dataset_target(source_path, plan)
    selected_output.write("\n[4/4] 确认任务\n")
    selected_output.write(f"  输入: {_display_path(source_path)}\n")
    selected_output.write(f"  YAML: {_display_path(selected_config)}\n")
    selected_output.write(f"  档位: {plan.label} [{plan.profile or 'flat'}]\n")
    selected_output.write(f"  抽帧: 每 {plan.stride} 帧取 1 帧\n")
    selected_output.write(f"  阶段: {', '.join(plan.stages)}\n")
    selected_output.write(f"  后端: {compute.backend.upper()}（请求 {plan.compute_backend}）\n")
    selected_output.write(
        f"  输出: {_display_path(target) if target is not None else _display_path(source_path)}\n"
    )
    if target is not None and target.exists():
        selected_output.write("  提示: 已有提取帧可复用，重建产物将按当前档位刷新。\n")

    if not assume_yes:
        answer = _ask(
            "确认开始重建？输入 y 继续 [y/N]: ",
            input_fn=selected_input,
            output=selected_output,
        ).lower()
        if answer not in {"y", "yes"}:
            selected_output.write("已取消，没有启动重建。\n")
            return 0

    arguments = reconstruction_arguments(source_path, plan, target)
    selected_output.write("\n开始重建。可按 Ctrl+C 安全中止当前任务。\n")
    selected_output.flush()
    if runner is not None:
        return int(runner(arguments))
    # Imported lazily to avoid a module cycle while cli.py dispatches `wizard`.
    from .cli import main

    return main(arguments)
