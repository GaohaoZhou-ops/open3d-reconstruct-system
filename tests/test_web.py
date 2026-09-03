from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from open3d_reconstruct.paths import ROOT
from open3d_reconstruct.web import (
    ControlCenter,
    WebActionError,
    _clean_recording_name,
    create_server,
)


def wait_for(controller: ControlCenter, phase: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = controller.snapshot()
        if state["phase"] == phase:
            return state
        time.sleep(0.02)
    raise AssertionError(
        f"等待状态 {phase!r} 超时；当前状态: {controller.snapshot()!r}"
    )


class WebNameTests(unittest.TestCase):
    def test_name_accepts_chinese_and_removes_known_extension(self) -> None:
        self.assertEqual(_clean_recording_name(" 客厅 扫描.bag "), "客厅-扫描")

    def test_name_rejects_path_separators(self) -> None:
        with self.assertRaises(WebActionError):
            _clean_recording_name("../outside")


class WebProgressTests(unittest.TestCase):
    def test_conversion_output_updates_structured_progress(self) -> None:
        controller = ControlCenter(ROOT / "open3d-reconstruct")
        try:
            with controller._lock:
                controller._task = "convert"
                controller._reset_conversion_progress_locked()
            controller._append_log("已提取 60 帧……")
            state = controller.snapshot()
            self.assertEqual(state["conversion"]["stage"], "extract")
            self.assertEqual(state["conversion"]["processed"], 60)

            controller._append_log("提取完成：data/example（143 帧，7.1 秒）")
            controller._append_log("数据集检查通过：143 帧")
            controller._append_log("[1/4] 生成局部片段")
            controller._append_log(
                "Fragment 000 / 001 :: RGBD matching between frame : 25 and 30"
            )
            progress = controller.snapshot()["conversion"]
            self.assertEqual(progress["stage"], "make")
            self.assertEqual(progress["stage_index"], 1)
            self.assertEqual(progress["frame_count"], 143)
            self.assertIn("片段 1/2", progress["detail"])
        finally:
            with controller._lock:
                controller._task = None
            controller.close()


class ControlCenterProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="web-controller-test-", dir=ROOT / ".cache"
        )
        self.temp_path = Path(self.temporary.name)
        self.launcher = self.temp_path / "fake-launcher"
        python = Path(os.sys.executable).resolve()
        self.launcher.write_text(
            f"""#!{python}
import signal
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if args[0] == "record":
    output = Path(args[args.index("--output") + 1])
    def finish(_signum, _frame):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"valid fake recording")
        print("fake recording finalized", flush=True)
        raise SystemExit(0)
    signal.signal(signal.SIGINT, finish)
    print("fake recording ready", flush=True)
    while True:
        time.sleep(0.05)
elif args[0] == "reconstruct":
    dataset = Path(args[args.index("--dataset") + 1])
    scene = dataset / "scene"
    scene.mkdir(parents=True, exist_ok=True)
    (scene / "integrated.ply").write_text(
        "ply\\nformat ascii 1.0\\nelement vertex 3\\n"
        "property float x\\nproperty float y\\nproperty float z\\n"
        "end_header\\n0 0 0\\n1 0 0\\n0 1 0\\n",
        encoding="ascii",
    )
    print("fake reconstruction complete", flush=True)
else:
    raise SystemExit(2)
""",
            encoding="utf-8",
        )
        self.launcher.chmod(0o755)
        self.controller = ControlCenter(self.launcher)
        self.created_paths: list[Path] = []

    def tearDown(self) -> None:
        self.controller.close()
        for path in self.created_paths:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        self.temporary.cleanup()

    def test_record_stop_then_convert_state_machine(self) -> None:
        name = f"web-unit-{uuid.uuid4().hex}"
        state = self.controller.start_recording(
            hardware="d435i", device=0, name=name
        )
        recording = ROOT / state["recording"]["path"]
        dataset = ROOT / state["dataset"]
        self.created_paths.extend((recording, dataset))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(
                "fake recording ready" in line["message"]
                for line in self.controller.snapshot()["logs"]
            ):
                break
            time.sleep(0.02)
        else:
            self.fail("伪录制进程没有准备好")

        stopping = self.controller.stop_recording()
        self.assertEqual(stopping["phase"], "stopping")
        recorded = wait_for(self.controller, "recorded")
        self.assertTrue(recorded["can_start_conversion"])
        self.assertGreater(recorded["recording"]["size"], 0)

        converting = self.controller.start_conversion(stride=2)
        self.assertIn(converting["phase"], {"converting", "completed"})
        completed = wait_for(self.controller, "completed")
        self.assertTrue(completed["mesh"]["exists"])
        self.assertEqual(completed["hardware"], "d435i")
        self.assertEqual(completed["conversion"]["status"], "completed")
        self.assertEqual(completed["conversion"]["artifact"]["kind"], "model")

    def test_controller_shutdown_safely_finalizes_active_recording(self) -> None:
        name = f"web-shutdown-{uuid.uuid4().hex}"
        state = self.controller.start_recording(
            hardware="azure-kinect", device=0, name=name
        )
        recording = ROOT / state["recording"]["path"]
        dataset = ROOT / state["dataset"]
        self.created_paths.extend((recording, dataset))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(
                "fake recording ready" in line["message"]
                for line in self.controller.snapshot()["logs"]
            ):
                break
            time.sleep(0.02)
        else:
            self.fail("伪录制进程没有准备好")

        self.controller.close()
        stopped = wait_for(self.controller, "recorded")
        self.assertTrue(stopped["recording"]["exists"])
        self.assertGreater(stopped["recording"]["size"], 0)


class WebHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ControlCenter(ROOT / "open3d-reconstruct")
        self.server = create_server(self.controller, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.controller.close()
        self.thread.join(timeout=2)

    def get(self, path: str) -> tuple[bytes, dict]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.read(), dict(response.headers)

    def test_page_and_assets_are_served_from_same_port(self) -> None:
        page, headers = self.get("/")
        script, _ = self.get("/app.js")
        style, _ = self.get("/app.css")
        self.assertIn("Azure Kinect DK".encode(), page)
        self.assertIn("开始录制".encode(), page)
        self.assertIn("结束录制".encode(), page)
        self.assertIn("开始转换".encode(), page)
        self.assertIn("运动传感器".encode(), page)
        self.assertIn(b"/api/record/start", script)
        self.assertIn(b"/api/live/state", script)
        self.assertIn(b"processViewer.load", script)
        self.assertIn(b".camera-card", style)
        self.assertIn(b".capture-visual", style)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_state_api_starts_idle(self) -> None:
        payload, _ = self.get("/api/state")
        value = json.loads(payload)
        self.assertTrue(value["ok"])
        self.assertEqual(value["state"]["phase"], "idle")
        self.assertEqual(value["state"]["logs"], [])
        self.assertIsNone(value["state"]["live"])
        self.assertIsNone(value["state"]["conversion"])

    def test_live_and_conversion_visual_endpoints(self) -> None:
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory(prefix="web-visual-test-") as temporary:
            root = Path(temporary)
            live = root / "live"
            live.mkdir()
            (live / "rgb.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            (live / "depth.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            (live / "state.json").write_text(
                json.dumps({"active": True, "sequence": 1, "imu": None}),
                encoding="utf-8",
            )
            dataset = root / "dataset"
            extracting = root / "dataset.extracting"
            (extracting / "color").mkdir(parents=True)
            (extracting / "depth").mkdir()
            (extracting / "fragments").mkdir()
            cv2.imwrite(
                str(extracting / "color" / "000000.jpg"),
                np.zeros((8, 10, 3), dtype=np.uint8),
            )
            cv2.imwrite(
                str(extracting / "depth" / "000000.png"),
                np.full((8, 10), 1000, dtype=np.uint16),
            )
            (extracting / "fragments" / "fragment_000.ply").write_text(
                "ply\nformat ascii 1.0\nelement vertex 1\n"
                "property float x\nproperty float y\nproperty float z\n"
                "end_header\n0 0 0\n",
                encoding="ascii",
            )
            with self.controller._lock:
                self.controller._live_dir = live
                self.controller._dataset = dataset
                self.controller._reset_conversion_progress_locked()

            live_state, _ = self.get("/api/live/state")
            self.assertEqual(json.loads(live_state)["live"]["sequence"], 1)
            live_rgb, headers = self.get("/api/live/rgb")
            self.assertEqual(live_rgb, b"\xff\xd8\xff\xd9")
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            process_rgb, headers = self.get("/api/process/rgb")
            self.assertTrue(process_rgb.startswith(b"\xff\xd8"))
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            process_depth, headers = self.get("/api/process/depth")
            self.assertTrue(process_depth.startswith(b"\xff\xd8"))
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            process_model, headers = self.get("/api/process/model")
            self.assertTrue(process_model.startswith(b"ply\n"))
            self.assertEqual(headers["Content-Type"], "model/ply")

            with self.controller._lock:
                self.controller._live_dir = None
                self.controller._dataset = None
                self.controller._conversion_progress = None

    def test_health_api_identifies_the_local_service(self) -> None:
        payload, _ = self.get("/api/health")
        value = json.loads(payload)
        self.assertTrue(value["ok"])
        self.assertEqual(value["service"], "open3d-reconstruct-web")
        self.assertEqual(value["phase"], "idle")
        self.assertEqual(value["port"], self.server.server_address[1])

    def test_post_requires_same_origin_marker(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/record/start",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 403)

    def test_invalid_camera_does_not_start_process(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/record/start",
            data=json.dumps({"hardware": "unknown"}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Open3D-Reconstruct": "web",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(self.controller.snapshot()["phase"], "idle")


if __name__ == "__main__":
    unittest.main()
