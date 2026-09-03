# ----------------------------------------------------------------------------
# -                        Open3D: www.open3d.org                            -
# ----------------------------------------------------------------------------
# Copyright (c) 2018-2024 www.open3d.org
# SPDX-License-Identifier: MIT
# ----------------------------------------------------------------------------

# examples/python/reconstruction_system/make_fragments.py

import json
import math
import multiprocessing
import os, sys
import numpy as np
import open3d as o3d

pyexample_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(pyexample_path)

from open3d_example import *

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from optimize_posegraph import optimize_posegraph_for_fragment

# check opencv python package
with_opencv = initialize_opencv()
if with_opencv:
    from opencv_pose_estimation import pose_estimation


WEB_MATCH_PREFIX = "__OPEN3D_WEB_MATCH__ "


def emit_web_match(status, fragment_id, source, target, kind, success=None,
                   transformation=None, information=None):
    """Emit compact, machine-readable odometry diagnostics for the Web UI."""
    if os.environ.get("OPEN3D_RECONSTRUCT_WEB_METRICS") != "1":
        return
    payload = {
        "status": status,
        "fragment": int(fragment_id),
        "source": int(source),
        "target": int(target),
        "kind": kind,
    }
    if status == "result":
        succeeded = bool(success)
        information_strength = 0.0
        translation_m = 0.0
        rotation_deg = 0.0
        if succeeded:
            information_strength = float(np.trace(information))
            translation_m = float(np.linalg.norm(transformation[:3, 3]))
            rotation_cosine = np.clip(
                (np.trace(transformation[:3, :3]) - 1.0) / 2.0,
                -1.0,
                1.0,
            )
            rotation_deg = float(np.degrees(np.arccos(rotation_cosine)))
        payload.update(
            success=succeeded,
            information=(
                information_strength if np.isfinite(information_strength) else 0.0
            ),
            translation_m=translation_m if np.isfinite(translation_m) else 0.0,
            rotation_deg=rotation_deg if np.isfinite(rotation_deg) else 0.0,
        )
    print(
        WEB_MATCH_PREFIX
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )


def register_one_rgbd_pair(s, t, color_files, depth_files, intrinsic,
                           with_opencv, config):
    source_rgbd_image = read_rgbd_image(color_files[s], depth_files[s], True,
                                        config)
    target_rgbd_image = read_rgbd_image(color_files[t], depth_files[t], True,
                                        config)

    option = o3d.pipelines.odometry.OdometryOption()
    option.depth_diff_max = config["depth_diff_max"]
    if abs(s - t) != 1:
        if with_opencv:
            success_5pt, odo_init = pose_estimation(source_rgbd_image,
                                                    target_rgbd_image,
                                                    intrinsic, False)
            if success_5pt:
                [success, trans, info
                ] = o3d.pipelines.odometry.compute_rgbd_odometry(
                    source_rgbd_image, target_rgbd_image, intrinsic, odo_init,
                    o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm(),
                    option)
                return [success, trans, info]
        return [False, np.identity(4), np.identity(6)]
    else:
        odo_init = np.identity(4)
        [success, trans, info] = o3d.pipelines.odometry.compute_rgbd_odometry(
            source_rgbd_image, target_rgbd_image, intrinsic, odo_init,
            o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm(), option)
        return [success, trans, info]


def make_posegraph_for_fragment(path_dataset, sid, eid, color_files,
                                depth_files, fragment_id, n_fragments,
                                intrinsic, with_opencv, config):
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    pose_graph = o3d.pipelines.registration.PoseGraph()
    trans_odometry = np.identity(4)
    pose_graph.nodes.append(
        o3d.pipelines.registration.PoseGraphNode(trans_odometry))
    for s in range(sid, eid):
        for t in range(s + 1, eid):
            # odometry
            if t == s + 1:
                print(
                    "Fragment %03d / %03d :: RGBD matching between frame : %d and %d"
                    % (fragment_id, n_fragments - 1, s, t), flush=True)
                emit_web_match("running", fragment_id, s, t, "odometry")
                [success, trans,
                 info] = register_one_rgbd_pair(s, t, color_files, depth_files,
                                                intrinsic, with_opencv, config)
                emit_web_match("result", fragment_id, s, t, "odometry",
                               success, trans, info)
                trans_odometry = np.dot(trans, trans_odometry)
                trans_odometry_inv = np.linalg.inv(trans_odometry)
                pose_graph.nodes.append(
                    o3d.pipelines.registration.PoseGraphNode(
                        trans_odometry_inv))
                pose_graph.edges.append(
                    o3d.pipelines.registration.PoseGraphEdge(s - sid,
                                                             t - sid,
                                                             trans,
                                                             info,
                                                             uncertain=False))

            # keyframe loop closure
            if s % config['n_keyframes_per_n_frame'] == 0 \
                    and t % config['n_keyframes_per_n_frame'] == 0:
                print(
                    "Fragment %03d / %03d :: RGBD matching between frame : %d and %d"
                    % (fragment_id, n_fragments - 1, s, t), flush=True)
                emit_web_match("running", fragment_id, s, t, "loop")
                [success, trans,
                 info] = register_one_rgbd_pair(s, t, color_files, depth_files,
                                                intrinsic, with_opencv, config)
                emit_web_match("result", fragment_id, s, t, "loop", success,
                               trans, info)
                if success:
                    pose_graph.edges.append(
                        o3d.pipelines.registration.PoseGraphEdge(
                            s - sid, t - sid, trans, info, uncertain=True))
    o3d.io.write_pose_graph(
        join(path_dataset, config["template_fragment_posegraph"] % fragment_id),
        pose_graph)


def integrate_rgb_frames_for_fragment(color_files, depth_files, fragment_id,
                                      n_fragments, pose_graph_name, intrinsic,
                                      config):
    pose_graph = o3d.io.read_pose_graph(pose_graph_name)
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=config["tsdf_cubic_size"] / 512.0,
        sdf_trunc=0.04,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    for i in range(len(pose_graph.nodes)):
        i_abs = fragment_id * config['n_frames_per_fragment'] + i
        print(
            "Fragment %03d / %03d :: integrate rgbd frame %d (%d of %d)." %
            (fragment_id, n_fragments - 1, i_abs, i + 1, len(pose_graph.nodes)),
            flush=True)
        rgbd = read_rgbd_image(color_files[i_abs], depth_files[i_abs], False,
                               config)
        pose = pose_graph.nodes[i].pose
        volume.integrate(rgbd, intrinsic, np.linalg.inv(pose))
    mesh = volume.extract_triangle_mesh()
    mesh.compute_vertex_normals()
    return mesh


def make_pointcloud_for_fragment(path_dataset, color_files, depth_files,
                                 fragment_id, n_fragments, intrinsic, config):
    mesh = integrate_rgb_frames_for_fragment(
        color_files, depth_files, fragment_id, n_fragments,
        join(path_dataset,
             config["template_fragment_posegraph_optimized"] % fragment_id),
        intrinsic, config)
    pcd = o3d.geometry.PointCloud()
    pcd.points = mesh.vertices
    pcd.colors = mesh.vertex_colors
    pcd_name = join(path_dataset,
                    config["template_fragment_pointcloud"] % fragment_id)
    o3d.io.write_point_cloud(pcd_name,
                             pcd,
                             format='auto',
                             write_ascii=False,
                             compressed=True)


def process_single_fragment(fragment_id, color_files, depth_files, n_files,
                            n_fragments, config):
    sid = fragment_id * config['n_frames_per_fragment']
    eid = min(sid + config['n_frames_per_fragment'], n_files)
    print("片段 %d/%d 开始：帧 %d–%d。" %
          (fragment_id + 1, n_fragments, sid, max(sid, eid - 1)),
          flush=True)
    if config["path_intrinsic"]:
        intrinsic = o3d.io.read_pinhole_camera_intrinsic(
            config["path_intrinsic"])
    else:
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            o3d.camera.PinholeCameraIntrinsicParameters.PrimeSenseDefault)
    make_posegraph_for_fragment(config["path_dataset"], sid, eid, color_files,
                                depth_files, fragment_id, n_fragments,
                                intrinsic, with_opencv, config)
    optimize_posegraph_for_fragment(config["path_dataset"], fragment_id, config)
    make_pointcloud_for_fragment(config["path_dataset"], color_files,
                                 depth_files, fragment_id, n_fragments,
                                 intrinsic, config)


def process_single_fragment_with_result(args):
    process_single_fragment(*args)
    return args[0]


def run(config):

    print("making fragments from RGBD sequence.", flush=True)
    make_clean_folder(join(config["path_dataset"], config["folder_fragment"]))

    [color_files, depth_files] = get_rgbd_file_lists(config["path_dataset"])
    n_files = len(color_files)
    n_fragments = int(
        math.ceil(float(n_files) / config['n_frames_per_fragment']))

    max_workers = (min(max(1, multiprocessing.cpu_count() - 1), n_fragments)
                   if config["python_multi_threading"] is True else 1)
    print("局部片段计划：%d 帧，%d 个片段，使用 %d 个并行进程。" %
          (n_files, n_fragments, max_workers), flush=True)

    if config["python_multi_threading"] is True:
        # Prevent over allocation of open mp threads in child processes
        os.environ['OMP_NUM_THREADS'] = '1'
        mp_context = multiprocessing.get_context('spawn')
        with mp_context.Pool(processes=max_workers) as pool:
            args = [(fragment_id, color_files, depth_files, n_files,
                     n_fragments, config) for fragment_id in range(n_fragments)]
            for completed, fragment_id in enumerate(
                    pool.imap_unordered(process_single_fragment_with_result,
                                        args), start=1):
                print("片段完成 %d/%d（片段 %d）。" %
                      (completed, n_fragments, fragment_id + 1), flush=True)
    else:
        for fragment_id in range(n_fragments):
            process_single_fragment(fragment_id, color_files, depth_files,
                                    n_files, n_fragments, config)
            print("片段完成 %d/%d（片段 %d）。" %
                  (fragment_id + 1, n_fragments, fragment_id + 1), flush=True)
