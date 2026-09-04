from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

from open3d_reconstruct.paths import RECORDINGS_DIR, ROOT
from open3d_reconstruct.web import (
    ControlCenter,
    WEB_MATCH_PREFIX,
    WebActionError,
    _clean_recording_name,
    _recording_import_spec,
    _validated_reconstruction_parameters,
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

    def test_recording_import_infers_backend_from_extension(self) -> None:
        self.assertEqual(
            _recording_import_spec("客厅扫描.MKV", "d435i"),
            ("客厅扫描", ".mkv", "azure-kinect"),
        )
        self.assertEqual(
            _recording_import_spec("desk.bag", "d435i"),
            ("desk", ".bag", "d435i"),
        )
        with self.assertRaises(WebActionError):
            _recording_import_spec("points.ply")


class WebReconstructionParameterTests(unittest.TestCase):
    def test_allowed_parameters_are_normalized(self) -> None:
        values = _validated_reconstruction_parameters(
            {
                "voxel_size": 0.03,
                "depth_max": 4,
                "n_frames_per_fragment": 80,
                "n_keyframes_per_n_frame": 5,
                "icp_method": "color",
                "global_registration": "fgr",
            }
        )
        self.assertEqual(values["voxel_size"], 0.03)
        self.assertEqual(values["depth_max"], 4.0)
        self.assertEqual(values["global_registration"], "fgr")

    def test_unknown_or_out_of_range_parameters_are_rejected(self) -> None:
        with self.assertRaises(WebActionError):
            _validated_reconstruction_parameters({"path_dataset": "/tmp/outside"})
        with self.assertRaises(WebActionError):
            _validated_reconstruction_parameters({"voxel_size": 0.001})
        with self.assertRaises(WebActionError):
            _validated_reconstruction_parameters(
                {
                    "n_frames_per_fragment": 30,
                    "n_keyframes_per_n_frame": 31,
                }
            )


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
            controller._append_log("局部片段计划：143 帧，2 个片段，使用 2 个并行进程。")
            controller._append_log("片段 1/2 开始：帧 0–99。")
            controller._append_log(
                WEB_MATCH_PREFIX
                + json.dumps(
                    {
                        "status": "running",
                        "fragment": 0,
                        "source": 0,
                        "target": 1,
                        "kind": "odometry",
                    }
                )
            )
            controller._append_log(
                WEB_MATCH_PREFIX
                + json.dumps(
                    {
                        "status": "result",
                        "fragment": 0,
                        "source": 0,
                        "target": 1,
                        "kind": "odometry",
                        "success": True,
                        "information": 1234.5,
                        "translation_m": 0.02,
                        "rotation_deg": 1.5,
                    }
                )
            )
            controller._append_log(
                WEB_MATCH_PREFIX
                + json.dumps(
                    {
                        "status": "result",
                        "fragment": 0,
                        "source": 0,
                        "target": 5,
                        "kind": "loop",
                        "success": False,
                        "information": 9999,
                    }
                )
            )
            controller._append_log("片段完成 1/2（片段 1）。")
            snapshot = controller.snapshot()
            progress = snapshot["conversion"]
            self.assertEqual(progress["stage"], "make")
            self.assertEqual(progress["stage_index"], 1)
            self.assertEqual(progress["frame_count"], 143)
            self.assertEqual(progress["fragment_completed"], 1)
            self.assertEqual(progress["fragment_total"], 2)
            self.assertIn("局部片段已完成 1/2", progress["detail"])
            self.assertIn("stage_elapsed_seconds", progress)
            self.assertIn("quiet_seconds", progress)
            self.assertEqual(progress["matching"]["expected"], 367)
            self.assertEqual(progress["matching"]["attempted"], 2)
            self.assertEqual(progress["matching"]["succeeded"], 1)
            self.assertEqual(progress["matching"]["failed"], 1)
            self.assertEqual(progress["matching"]["events"][0]["information"], 1234.5)
            self.assertEqual(progress["matching"]["events"][1]["information"], 0.0)
            self.assertEqual(progress["matching"]["active"], [])
            self.assertFalse(
                any(
                    line["message"].startswith(WEB_MATCH_PREFIX)
                    for line in snapshot["logs"]
                )
            )
        finally:
            with controller._lock:
                controller._task = None
            controller.close()


class WebRecordingManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ControlCenter(ROOT / "open3d-reconstruct")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="open3d-recording-reference-test-"
        )
        self.created_recordings: list[Path] = []
        self.created_datasets: list[Path] = []

    def tearDown(self) -> None:
        self.controller.close()
        for path in self.created_recordings:
            path.unlink(missing_ok=True)
        for path in self.created_datasets:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
        self.temporary.cleanup()

    def source(self, extension: str = ".mkv") -> Path:
        path = Path(self.temporary.name) / f"zero-copy-{uuid.uuid4().hex}{extension}"
        path.write_bytes(b"synthetic local recording")
        return path

    def test_local_recording_uses_hardlink_without_copying_bytes(self) -> None:
        source = self.source()
        state = self.controller.reference_local_recording(source)
        managed = ROOT / state["recording"]["path"]
        self.created_recordings.append(managed)
        dataset = ROOT / state["dataset"]
        self.created_datasets.append(dataset)

        self.assertEqual(state["phase"], "recorded")
        self.assertTrue(os.path.samefile(source, managed))
        self.assertEqual(source.stat().st_ino, managed.stat().st_ino)
        item = next(
            item
            for item in self.controller.recordings_snapshot()["items"]
            if item["name"] == managed.name
        )
        self.assertEqual(item["storage_mode"], "hardlink")
        self.assertEqual(item["additional_bytes"], 0)

        result = self.controller.delete_managed_recording(managed.name)
        self.assertFalse(managed.exists())
        self.assertTrue(source.exists())
        self.assertTrue(result["removed"]["external_source_preserved"])

    def test_cross_device_reference_falls_back_to_symlink(self) -> None:
        source = self.source(".bag")
        with mock.patch(
            "open3d_reconstruct.web.os.link",
            side_effect=OSError(errno.EXDEV, "cross-device link"),
        ):
            state = self.controller.reference_local_recording(
                source, hardware="d435i"
            )
        managed = ROOT / state["recording"]["path"]
        self.created_recordings.append(managed)
        self.created_datasets.append(ROOT / state["dataset"])

        self.assertTrue(managed.is_symlink())
        self.assertEqual(state["hardware"], "d435i")
        self.assertTrue(self.controller.result_file("recording").is_symlink())
        result = self.controller.delete_managed_recording(managed.name)
        self.assertTrue(source.exists())
        self.assertTrue(result["removed"]["external_source_preserved"])

    def test_native_picker_selection_is_registered_without_upload(self) -> None:
        source = self.source()
        completed = subprocess.CompletedProcess(
            args=["zenity"], returncode=0, stdout=f"{source}\n", stderr=""
        )
        with (
            mock.patch(
                "open3d_reconstruct.web.shutil.which",
                return_value="/usr/bin/zenity",
            ),
            mock.patch(
                "open3d_reconstruct.web.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            state = self.controller.choose_local_recording()
        self.assertIsNotNone(state)
        assert state is not None
        managed = ROOT / state["recording"]["path"]
        self.created_recordings.append(managed)
        self.created_datasets.append(ROOT / state["dataset"])
        self.assertTrue(os.path.samefile(source, managed))
        self.assertIn("--file-selection", run.call_args.args[0])
        self.controller.delete_managed_recording(managed.name)

    def test_delete_can_keep_or_remove_associated_outputs(self) -> None:
        source = self.source()
        state = self.controller.reference_local_recording(source)
        managed = ROOT / state["recording"]["path"]
        dataset = ROOT / state["dataset"]
        self.created_recordings.append(managed)
        self.created_datasets.append(dataset)
        (dataset / "color").mkdir(parents=True)
        (dataset / "color" / "000000.jpg").write_bytes(b"rgb")
        (dataset / "scene").mkdir()
        (dataset / "scene" / "integrated.ply").write_bytes(b"mesh")

        restored = self.controller.select_managed_recording(managed.name)
        self.assertEqual(restored["phase"], "completed")
        kept = self.controller.delete_managed_recording(
            managed.name, delete_outputs=False
        )
        self.assertTrue(dataset.is_dir())
        self.assertFalse(kept["removed"]["outputs_deleted"])

        source_two = self.source()
        state_two = self.controller.reference_local_recording(source_two)
        managed_two = ROOT / state_two["recording"]["path"]
        dataset_two = ROOT / state_two["dataset"]
        self.created_recordings.append(managed_two)
        self.created_datasets.append(dataset_two)
        (dataset_two / "depth").mkdir(parents=True)
        (dataset_two / "depth" / "000000.png").write_bytes(b"depth")
        removed = self.controller.delete_managed_recording(
            managed_two.name, delete_outputs=True
        )
        self.assertFalse(dataset_two.exists())
        self.assertTrue(removed["removed"]["outputs_deleted"])
        self.assertGreater(removed["removed"]["output_bytes"], 0)

    def test_managed_recording_rejects_path_traversal(self) -> None:
        with self.assertRaises(WebActionError):
            self.controller.select_managed_recording("../outside.mkv")
        with self.assertRaises(WebActionError):
            self.controller.delete_managed_recording("not-a-video.txt")


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

        converting = self.controller.start_conversion(
            stride=2,
            parameters={
                "voxel_size": 0.03,
                "depth_max": 3.0,
                "n_frames_per_fragment": 80,
                "n_keyframes_per_n_frame": 5,
                "icp_method": "color",
                "global_registration": "ransac",
                "depth_diff_max": 0.07,
            },
        )
        self.assertIn(converting["phase"], {"converting", "completed"})
        self.assertEqual(converting["reconstruction_settings"]["stride"], 2)
        self.assertEqual(
            converting["reconstruction_settings"]["n_frames_per_fragment"], 80
        )
        completed = wait_for(self.controller, "completed")
        self.assertTrue(completed["mesh"]["exists"])
        self.assertEqual(completed["hardware"], "d435i")
        self.assertEqual(completed["conversion"]["status"], "completed")
        self.assertEqual(completed["conversion"]["artifact"]["kind"], "model")
        self.assertTrue(
            any(
                "--set voxel_size=0.03" in line["message"]
                for line in completed["logs"]
            )
        )

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

    def post_json(self, path: str, value: dict) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(value).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Open3D-Reconstruct": "web",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_page_and_assets_are_served_from_same_port(self) -> None:
        page, headers = self.get("/")
        script, _ = self.get("/app.js")
        style, _ = self.get("/app.css")
        self.assertIn("Azure Kinect DK".encode(), page)
        self.assertIn("连接并录制".encode(), page)
        self.assertIn("结束录制".encode(), page)
        self.assertIn("打开本地录制".encode(), page)
        self.assertIn("管理录制".encode(), page)
        self.assertIn("视频及全部产物".encode(), page)
        self.assertNotIn(b'id="recording-file"', page)
        self.assertIn("开始重建".encode(), page)
        self.assertIn("运动数据".encode(), page)
        self.assertIn("确认重建参数".encode(), page)
        self.assertIn(b"imu-orientation", page)
        self.assertIn(b"process-activity", page)
        self.assertIn(b"matching-heatmap", page)
        self.assertIn(b"model-viewer-orientation", page)
        self.assertIn(b"model-mesh-viewer", page)
        self.assertIn(b"model-viewer-navigation", page)
        self.assertIn(b"model-viewer-scale", page)
        self.assertIn(b"model-viewer-bounds", page)
        self.assertIn("点云".encode(), page)
        self.assertIn("网格".encode(), page)
        self.assertIn("彩色面".encode(), page)
        self.assertIn("结构面".encode(), page)
        self.assertIn("平移".encode(), page)
        self.assertIn("中心平面".encode(), page)
        self.assertIn(b"process-viewer-orientation", page)
        self.assertIn("信息矩阵迹".encode(), page)
        self.assertIn(b"empty-stage", page)
        self.assertNotIn(b"hero", page)
        self.assertIn(b"live-rgb-buffer", page)
        self.assertIn(b"live-depth-buffer", page)
        self.assertIn(b"/api/record/start", script)
        self.assertIn(b"/api/live/state", script)
        self.assertIn(b"class LiveFrameBuffer", script)
        self.assertIn(b"class ImuOrientation", script)
        self.assertIn(b"class MatchingDiagnostics", script)
        self.assertIn(b"class ViewerOrientation", script)
        self.assertIn(b"class MeshRenderer", script)
        self.assertIn(b"meshNormals", script)
        self.assertIn(b"gl.drawElements", script)
        self.assertIn(b"viewerNavigationButtons", script)
        self.assertIn(b"gl.uniform2f", script)
        self.assertIn(b"this.panX", script)
        self.assertNotIn(b'addEventListener("dblclick"', script)
        self.assertIn(b'data-viewer-navigation="reset"', page)
        self.assertNotIn("双击复位".encode(), page)
        self.assertIn(b"niceScaleLength", script)
        self.assertIn(b"formatSceneDimensions", script)
        self.assertIn(b"this.modelExtent", script)
        self.assertIn(b"mesh_preview_url", script)
        self.assertIn(b"/api/recording/select-local", script)
        self.assertIn(b"/api/recordings/delete", script)
        self.assertNotIn(b"function uploadRecording", script)
        self.assertIn(b"wrapRadians(this.pitch", script)
        self.assertIn(b"reconstructionRequest", script)
        self.assertIn(b"processViewer.load", script)
        self.assertIn(b".camera-card", style)
        self.assertIn(b".capture-visual", style)
        self.assertIn(b".live-frame.active", style)
        self.assertIn(b".viewer-navigation", style)
        self.assertIn(b".viewer-scale-bar", style)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_state_api_starts_idle(self) -> None:
        payload, _ = self.get("/api/state")
        value = json.loads(payload)
        self.assertTrue(value["ok"])
        self.assertEqual(value["state"]["phase"], "idle")
        self.assertTrue(value["state"]["can_import_recording"])
        self.assertEqual(value["state"]["logs"], [])
        self.assertIsNone(value["state"]["live"])
        self.assertIsNone(value["state"]["conversion"])

    def test_existing_recording_can_be_streamed_into_the_project(self) -> None:
        name = f"web-import-{uuid.uuid4().hex}.mkv"
        content = b"synthetic azure kinect recording payload"
        query = urllib.parse.urlencode({"filename": name, "hardware": "d435i"})
        request = urllib.request.Request(
            f"{self.base}/api/recording/import?{query}",
            data=content,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Open3D-Reconstruct": "web",
            },
            method="POST",
        )
        imported: Path | None = None
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                value = json.loads(response.read())
            state = value["state"]
            imported = ROOT / state["recording"]["path"]
            self.assertTrue(value["ok"])
            self.assertEqual(state["phase"], "recorded")
            self.assertEqual(state["hardware"], "azure-kinect")
            self.assertEqual(state["task"], None)
            self.assertTrue(state["can_start_conversion"])
            self.assertEqual(imported.read_bytes(), content)
            self.assertFalse(
                list(RECORDINGS_DIR.glob(f".{imported.stem}.importing-*"))
            )
        finally:
            if imported is not None:
                imported.unlink(missing_ok=True)

    def test_managed_recording_can_be_listed_selected_and_deleted(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        recording = RECORDINGS_DIR / f"web-manager-{uuid.uuid4().hex}.mkv"
        recording.write_bytes(b"managed recording fixture")
        try:
            payload, _ = self.get("/api/recordings")
            recordings = json.loads(payload)["recordings"]
            item = next(
                item for item in recordings["items"] if item["name"] == recording.name
            )
            self.assertTrue(item["can_use"])
            self.assertEqual(item["storage_mode"], "owned")

            selected = self.post_json(
                "/api/recordings/use", {"name": recording.name}
            )
            self.assertEqual(selected["state"]["phase"], "recorded")
            self.assertEqual(
                Path(selected["state"]["recording"]["path"]).name,
                recording.name,
            )

            removed = self.post_json(
                "/api/recordings/delete",
                {"name": recording.name, "delete_outputs": False},
            )
            self.assertFalse(recording.exists())
            self.assertFalse(removed["removed"]["outputs_deleted"])
        finally:
            recording.unlink(missing_ok=True)

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
            cv2.imwrite(
                str(extracting / "color" / "000001.jpg"),
                np.full((8, 10, 3), 220, dtype=np.uint8),
            )
            cv2.imwrite(
                str(extracting / "depth" / "000001.png"),
                np.full((8, 10), 1800, dtype=np.uint16),
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
            first_rgb, _ = self.get("/api/process/rgb?frame=0")
            second_rgb, _ = self.get("/api/process/rgb?frame=1")
            self.assertNotEqual(first_rgb, second_rgb)
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

    def test_triangle_mesh_preview_is_generated_and_cached(self) -> None:
        (ROOT / ".cache").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="web-mesh-preview-test-", dir=ROOT / ".cache"
        ) as temporary:
            source = Path(temporary) / "integrated.ply"
            source.write_text(
                "ply\nformat ascii 1.0\n"
                "element vertex 4\n"
                "property float x\nproperty float y\nproperty float z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\n"
                "element face 2\nproperty list uchar uint vertex_indices\n"
                "end_header\n"
                "0 0 0 255 0 0\n1 0 0 0 255 0\n"
                "1 1 0 0 0 255\n0 1 0 255 255 255\n"
                "3 0 1 2\n3 0 2 3\n",
                encoding="ascii",
            )
            with self.controller._lock:
                self.controller._mesh = source

            state_payload, _ = self.get("/api/state")
            state = json.loads(state_payload)["state"]
            self.assertEqual(state["mesh_url"], "/api/files/mesh")
            self.assertEqual(
                state["mesh_preview_url"], "/api/files/mesh-preview"
            )

            with mock.patch(
                "open3d_reconstruct.web.MESH_PREVIEW_TARGET_TRIANGLES", 1
            ):
                preview_payload, headers = self.get("/api/files/mesh-preview")
            self.assertTrue(preview_payload.startswith(b"ply\n"))
            self.assertIn(b"element face 1\n", preview_payload)
            self.assertEqual(headers["Content-Type"], "model/ply")
            preview = source.with_name("integrated.preview-v2.ply")
            self.assertTrue(preview.is_file())
            preview_mtime = preview.stat().st_mtime_ns

            cached_payload, _ = self.get("/api/files/mesh-preview")
            self.assertEqual(cached_payload, preview_payload)
            self.assertEqual(preview.stat().st_mtime_ns, preview_mtime)
            self.assertEqual(
                sum(
                    line["message"].startswith("正在生成浏览器网格预览")
                    for line in self.controller.snapshot()["logs"]
                ),
                1,
            )
            with self.controller._lock:
                self.controller._mesh = None

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
