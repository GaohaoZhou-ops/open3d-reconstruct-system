from __future__ import annotations

import errno
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from . import __version__
from .paths import ROOT, RUNTIME_DIR, ensure_local_directories


SERVICE_NAME = "open3d-reconstruct-web"
DEFAULT_SERVICE_PORT = 11920
RUNTIME_ENVIRONMENT = "OPEN3D_RECONSTRUCT_RUNTIME_DIR"


@dataclass(frozen=True)
class ServiceFiles:
    runtime_dir: Path

    @property
    def lock(self) -> Path:
        return self.runtime_dir / "web.lock"

    @property
    def metadata(self) -> Path:
        return self.runtime_dir / "web.pid.json"

    @property
    def log(self) -> Path:
        return self.runtime_dir / "web.log"


@dataclass(frozen=True)
class ServiceMetadata:
    pid: int
    process_start_ticks: int | None
    port: int
    instance_id: str
    started_at: str
    version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": SERVICE_NAME,
            "pid": self.pid,
            "process_start_ticks": self.process_start_ticks,
            "port": self.port,
            "instance_id": self.instance_id,
            "started_at": self.started_at,
            "version": self.version,
        }


@dataclass(frozen=True)
class ServiceStatus:
    condition: str
    metadata: ServiceMetadata | None
    health: dict[str, Any] | None
    detail: str | None = None

    @property
    def running(self) -> bool:
        return self.condition == "running"


def service_files(runtime_dir: Path | None = None) -> ServiceFiles:
    configured = runtime_dir
    if configured is None:
        value = os.environ.get(RUNTIME_ENVIRONMENT)
        configured = Path(value) if value else RUNTIME_DIR
    resolved = configured.expanduser().resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"服务运行目录必须位于当前项目内: {resolved}") from exc
    return ServiceFiles(resolved)


def _try_lock(lock_file) -> bool:
    """Acquire the first byte of a project-local lock without blocking."""
    if os.name == "nt":
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock(lock_file) -> None:
    if os.name == "nt":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _windows_process_stat(pid: int) -> tuple[int, str] | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        exit_code = wintypes.DWORD()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ) or not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        created = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return created, "R" if exit_code.value == still_active else "Z"
    finally:
        kernel32.CloseHandle(handle)


def _process_stat(pid: int) -> tuple[int, str] | None:
    if os.name == "nt":
        return _windows_process_stat(pid)
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        if sys.platform != "darwin":
            return None
        try:
            result = subprocess.run(
                ["/bin/ps", "-o", "state=", "-o", "lstart=", "-p", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="ascii",
                errors="replace",
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        fields = result.stdout.strip().split(maxsplit=1)
        if result.returncode != 0 or len(fields) != 2:
            return None
        state, started = fields
        token = int.from_bytes(
            hashlib.blake2s(started.encode("ascii"), digest_size=8).digest(),
            byteorder="big",
        )
        return token, state[:1]
    end = value.rfind(")")
    if end < 0:
        return None
    fields = value[end + 2 :].split()
    # fields starts at proc(5)'s field 3 (state); starttime is field 22.
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19]), fields[0]
    except ValueError:
        return None


def process_matches(metadata: ServiceMetadata) -> bool:
    if metadata.pid <= 1:
        return False
    stat = _process_stat(metadata.pid)
    if stat is not None:
        start_ticks, state = stat
        if state == "Z":
            return False
        if metadata.process_start_ticks is not None:
            return start_ticks == metadata.process_start_ticks
        return True
    if os.name == "nt":
        return False
    try:
        os.kill(metadata.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_metadata(files: ServiceFiles | None = None) -> ServiceMetadata | None:
    selected = files or service_files()
    try:
        value = json.loads(selected.metadata.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("service") != SERVICE_NAME:
            return None
        pid = int(value["pid"])
        start_value = value.get("process_start_ticks")
        start_ticks = int(start_value) if start_value is not None else None
        port = int(value["port"])
        instance_id = str(value["instance_id"])
        started_at = str(value["started_at"])
        version = str(value.get("version", "unknown"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if pid <= 1 or not 1 <= port <= 65535 or not instance_id:
        return None
    return ServiceMetadata(
        pid=pid,
        process_start_ticks=start_ticks,
        port=port,
        instance_id=instance_id,
        started_at=started_at,
        version=version,
    )


class SingletonLease:
    """An advisory lock held for the full lifetime of one web process."""

    def __init__(self, *, port: int, files: ServiceFiles | None = None) -> None:
        self.port = port
        self.files = files or service_files()
        self.metadata: ServiceMetadata | None = None
        self._lock_file = None

    def __enter__(self) -> "SingletonLease":
        self.files.runtime_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.files.lock,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        lock_file = os.fdopen(descriptor, "r+", encoding="ascii")
        if not _try_lock(lock_file):
            lock_file.close()
            existing = read_metadata(self.files)
            detail = f"（PID {existing.pid}）" if existing else ""
            raise RuntimeError(f"Web 服务已有一个实例正在运行{detail}")

        self._lock_file = lock_file
        stat = _process_stat(os.getpid())
        self.metadata = ServiceMetadata(
            pid=os.getpid(),
            process_start_ticks=stat[0] if stat else None,
            port=self.port,
            instance_id=uuid.uuid4().hex,
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            version=__version__,
        )
        try:
            self._write_metadata()
        except Exception:
            _unlock(lock_file)
            lock_file.close()
            self._lock_file = None
            raise
        return self

    def _write_metadata(self) -> None:
        assert self.metadata is not None
        temporary = self.files.runtime_dir / (
            f".{self.files.metadata.name}.{self.metadata.instance_id}.tmp"
        )
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.metadata.as_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.files.metadata)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def __exit__(self, _type, _value, _traceback) -> None:
        if self.metadata is not None:
            current = read_metadata(self.files)
            if current and current.instance_id == self.metadata.instance_id:
                try:
                    self.files.metadata.unlink()
                except FileNotFoundError:
                    pass
        if self._lock_file is not None:
            _unlock(self._lock_file)
            self._lock_file.close()
            self._lock_file = None


def _query_health(
    metadata: ServiceMetadata, *, timeout: float = 0.8
) -> tuple[dict[str, Any] | None, str | None]:
    url = f"http://127.0.0.1:{metadata.port}/api/health"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            payload = response.read(64 * 1024 + 1)
            if len(payload) > 64 * 1024:
                return None, "健康检查响应过大"
            value = json.loads(payload)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "健康检查响应不是 JSON 对象"
    matches = (
        value.get("ok") is True
        and value.get("service") == SERVICE_NAME
        and value.get("pid") == metadata.pid
        and value.get("instance_id") == metadata.instance_id
    )
    if not matches:
        return None, "11920 端口响应的不是该服务实例"
    return value, None


def inspect_service(files: ServiceFiles | None = None) -> ServiceStatus:
    selected = files or service_files()
    metadata = read_metadata(selected)
    if metadata is None:
        if selected.metadata.exists():
            return ServiceStatus("stale", None, None, "PID 元数据损坏或不完整")
        return ServiceStatus("stopped", None, None)
    if not process_matches(metadata):
        return ServiceStatus("stale", metadata, None, "PID 文件对应的进程已经退出")
    health, error = _query_health(metadata)
    if health is None:
        return ServiceStatus("unhealthy", metadata, None, error)
    return ServiceStatus("running", metadata, health)


def _cleanup_stale_metadata(files: ServiceFiles) -> bool:
    files.runtime_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(files.lock, os.O_RDWR | os.O_CREAT, 0o600)
    lock_file = os.fdopen(descriptor, "r+", encoding="ascii")
    acquired = False
    try:
        acquired = _try_lock(lock_file)
        if not acquired:
            return False
        current = read_metadata(files)
        if current is None or not process_matches(current):
            try:
                files.metadata.unlink()
            except FileNotFoundError:
                pass
            return True
        return False
    finally:
        if acquired:
            _unlock(lock_file)
        lock_file.close()


def _tail_log(path: Path, *, lines: int = 12) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 32 * 1024))
            value = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(value.splitlines()[-lines:])


def _print_running(status: ServiceStatus, files: ServiceFiles) -> None:
    assert status.metadata is not None
    health = status.health or {}
    phase = health.get("phase", "unknown")
    task = health.get("task") or "无"
    print("Web 服务正在运行。")
    print(f"  PID:  {status.metadata.pid}")
    print(f"  地址: http://127.0.0.1:{status.metadata.port}/")
    print(f"  状态: {phase}（当前任务: {task}）")
    print(f"  启动: {status.metadata.started_at}")
    print(f"  日志: {files.log}")


def _launcher_prefix(launcher: Path | None) -> tuple[list[str], Path | None]:
    if launcher is None:
        return (
            [
                sys.executable,
                "-B",
                "-I",
                "-u",
                "-X",
                "utf8",
                "-m",
                "open3d_reconstruct",
            ],
            None,
        )
    executable = launcher.expanduser().resolve()
    if not executable.is_file():
        return [], executable
    suffix = executable.suffix.lower()
    if suffix == ".py":
        return [sys.executable, "-B", "-u", "-X", "utf8", str(executable)], None
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(executable)], None
    if os.name != "nt" and not os.access(executable, os.X_OK):
        return [], executable
    return [str(executable)], None


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _request_shutdown(metadata: ServiceMetadata, *, timeout: float = 2.0) -> bool:
    request = Request(
        f"http://127.0.0.1:{metadata.port}/api/service/shutdown",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Open3D-Reconstruct": "web",
            "X-Open3D-Reconstruct-Service": metadata.instance_id,
        },
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            value = json.loads(response.read(64 * 1024 + 1))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("ok") is True


def start_service(
    *,
    port: int = DEFAULT_SERVICE_PORT,
    files: ServiceFiles | None = None,
    launcher: Path | None = None,
    startup_timeout: float = 15.0,
) -> int:
    ensure_local_directories()
    selected = files or service_files()
    selected.runtime_dir.mkdir(parents=True, exist_ok=True)
    current = inspect_service(selected)
    if current.running:
        print("Web 服务已经启动；不会创建第二个实例。")
        _print_running(current, selected)
        return 0
    if current.condition == "unhealthy" and current.metadata is not None:
        print(
            f"错误: 服务进程 PID {current.metadata.pid} 仍存在，但健康检查失败: "
            f"{current.detail}",
            file=sys.stderr,
        )
        return 1
    _cleanup_stale_metadata(selected)

    prefix, invalid_launcher = _launcher_prefix(launcher)
    if invalid_launcher is not None:
        print(f"错误: 项目启动器不可执行: {invalid_launcher}", file=sys.stderr)
        return 1
    if not 1 <= port <= 65535:
        print("错误: 端口必须在 1 到 65535 之间", file=sys.stderr)
        return 1

    command = [*prefix, "web", "--no-browser", "--port", str(port)]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment[RUNTIME_ENVIRONMENT] = str(selected.runtime_dir)
    separator = (
        "\n"
        + "=" * 72
        + f"\n{datetime.now().astimezone().isoformat(timespec='seconds')} "
        + _format_command(command)
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        selected.log,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "ab", buffering=0) as log_stream:
        log_stream.write(separator)
        try:
            process_options: dict[str, Any] = {}
            if os.name == "nt":
                process_options["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                )
            else:
                process_options["start_new_session"] = True
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **process_options,
            )
        except OSError as exc:
            print(f"错误: 无法启动 Web 服务: {exc}", file=sys.stderr)
            return 1

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        status = inspect_service(selected)
        if status.running:
            print("Web 服务启动成功。")
            _print_running(status, selected)
            return 0
        returncode = process.poll()
        if returncode is not None:
            # A simultaneous start may have won the singleton race.
            status = inspect_service(selected)
            if status.running:
                print("Web 服务已经由另一个启动请求成功拉起。")
                _print_running(status, selected)
                return 0
            print(f"错误: Web 服务启动失败（退出代码 {returncode}）。", file=sys.stderr)
            tail = _tail_log(selected.log)
            if tail:
                print(tail, file=sys.stderr)
            return 1
        time.sleep(0.1)

    print("错误: Web 服务启动健康检查超时。", file=sys.stderr)
    metadata = read_metadata(selected)
    if metadata is not None:
        _request_shutdown(metadata)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
    return 1


def status_service(*, files: ServiceFiles | None = None, quiet: bool = False) -> int:
    selected = files or service_files()
    status = inspect_service(selected)
    if status.running:
        if not quiet:
            _print_running(status, selected)
        return 0
    if status.condition == "unhealthy" and status.metadata is not None:
        if not quiet:
            print(f"Web 服务进程存在，但健康检查失败（PID {status.metadata.pid}）。")
            print(f"  原因: {status.detail}")
            print(f"  日志: {selected.log}")
        return 1
    if not quiet:
        print("Web 服务未运行。")
        if status.detail:
            print(f"  提示: {status.detail}")
        print(f"  日志: {selected.log}")
    return 3


def stop_service(
    *, files: ServiceFiles | None = None, shutdown_timeout: float = 30.0
) -> int:
    selected = files or service_files()
    status = inspect_service(selected)
    metadata = status.metadata
    if metadata is None or not process_matches(metadata):
        _cleanup_stale_metadata(selected)
        print("Web 服务已经处于停止状态。")
        return 0

    print(f"正在停止 Web 服务（PID {metadata.pid}）……")
    requested = _request_shutdown(metadata)
    if not requested:
        if os.name == "nt":
            print(
                "错误: 服务未接受安全停止请求；为避免损坏正在封装的录制文件，"
                "未强制终止进程。",
                file=sys.stderr,
            )
            return 1
        try:
            os.kill(metadata.pid, signal.SIGINT)
        except ProcessLookupError:
            _cleanup_stale_metadata(selected)
            print("Web 服务已经停止。")
            return 0
        except PermissionError as exc:
            print(f"错误: 无法向服务进程发送停止信号: {exc}", file=sys.stderr)
            return 1

    deadline = time.monotonic() + shutdown_timeout
    while time.monotonic() < deadline:
        if not process_matches(metadata):
            _cleanup_stale_metadata(selected)
            print("Web 服务已安全停止。")
            return 0
        time.sleep(0.1)
    print(
        "错误: 服务未在超时时间内退出；为避免损坏正在封装的录制文件，未强制杀死进程。",
        file=sys.stderr,
    )
    print(f"请检查日志: {selected.log}", file=sys.stderr)
    return 1
