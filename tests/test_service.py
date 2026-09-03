from __future__ import annotations

import contextlib
import io
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from open3d_reconstruct.paths import ROOT
from open3d_reconstruct.service import (
    ServiceFiles,
    SingletonLease,
    inspect_service,
    process_matches,
    read_metadata,
    start_service,
    status_service,
    stop_service,
)


def unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class SingletonLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="service-lease-test-", dir=ROOT / ".cache"
        )
        self.files = ServiceFiles(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_lock_rejects_second_instance_and_cleans_pid_metadata(self) -> None:
        with SingletonLease(port=11920, files=self.files) as first:
            metadata = read_metadata(self.files)
            self.assertIsNotNone(metadata)
            assert metadata is not None
            self.assertEqual(metadata.instance_id, first.metadata.instance_id)
            self.assertTrue(process_matches(metadata))
            with self.assertRaisesRegex(RuntimeError, "已有一个实例"):
                with SingletonLease(port=11920, files=self.files):
                    self.fail("第二个单例锁不应成功")
        self.assertFalse(self.files.metadata.exists())
        self.assertTrue(self.files.lock.exists())


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="service-lifecycle-test-", dir=ROOT / ".cache"
        )
        self.files = ServiceFiles(Path(self.temporary.name))
        self.port = unused_local_port()

    def tearDown(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            stop_service(files=self.files, shutdown_timeout=5)
        # Popen construction reaps any completed child retained by subprocess.
        subprocess.run(["/usr/bin/true"], check=True)
        self.temporary.cleanup()

    def test_background_start_is_idempotent_and_stop_is_graceful(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            started = start_service(
                port=self.port,
                files=self.files,
                launcher=ROOT / "open3d-reconstruct",
                startup_timeout=8,
            )
        self.assertEqual(started, 0, output.getvalue())
        first = inspect_service(self.files)
        self.assertTrue(first.running, first)
        assert first.metadata is not None
        first_pid = first.metadata.pid

        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            duplicate = start_service(
                port=self.port,
                files=self.files,
                launcher=ROOT / "open3d-reconstruct",
                startup_timeout=8,
            )
        self.assertEqual(duplicate, 0, output.getvalue())
        second = inspect_service(self.files)
        self.assertEqual(second.metadata.pid, first_pid)
        self.assertEqual(status_service(files=self.files, quiet=True), 0)

        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            stopped = stop_service(files=self.files, shutdown_timeout=8)
        self.assertEqual(stopped, 0, output.getvalue())
        self.assertEqual(status_service(files=self.files, quiet=True), 3)
        self.assertFalse(self.files.metadata.exists())
        self.assertIn("Web 控制台已启动", self.files.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
