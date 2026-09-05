from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class OdometryResult:
    success: bool
    transformation: np.ndarray
    information: np.ndarray
    correspondences: int
    fitness: float
    inlier_rmse: float
    elapsed_seconds: float
    backend: str
    detail: str


@dataclass
class _PyramidLevel:
    intensity: Any
    depth: Any
    intensity_dx: Any
    intensity_dy: Any
    depth_dx: Any
    depth_dy: Any
    source_points: Any
    source_intensity: Any
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


@dataclass
class _FramePyramid:
    levels: tuple[_PyramidLevel, ...]


class TorchRGBDOdometry:
    """Open3D-compatible hybrid RGB-D odometry using portable Torch ops.

    The same tensor code runs on CUDA on Linux and Metal/MPS on Apple Silicon.
    It follows Open3D's legacy hybrid Jacobian: a photometric residual plus a
    projective depth residual. No device value is ever passed into legacy
    Open3D, whose macOS wheel is CPU-only.
    """

    _DIVISORS = (4, 2, 1)
    _ITERATIONS = (20, 10, 5)
    _MPS_ITERATIONS = (15, 7, 4)
    _LAMBDA_DEPTH = 0.968

    def __init__(self, backend: str, *, cache_size: int = 12) -> None:
        import torch
        import torch.nn.functional as functional

        if backend not in {"cpu", "cuda", "mps"}:
            raise ValueError(f"Torch RGB-D 里程计不支持设备: {backend}")
        self.torch = torch
        self.functional = functional
        self.backend = backend
        self.device = torch.device(backend)
        # Metal dispatch overhead dominates very small RGB-D linear systems.
        # This MPS budget keeps sub-millimetre agreement on representative
        # frames while CUDA retains Open3D's full iteration schedule.
        self.iterations = (
            self._MPS_ITERATIONS if backend == "mps" else self._ITERATIONS
        )
        self.cache_size = max(2, int(cache_size))
        self._cache: OrderedDict[tuple[Any, ...], _FramePyramid] = OrderedDict()
        self._gaussian_kernel = torch.tensor(
            [[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]],
            dtype=torch.float32,
            device=self.device,
        )[None, None] / 16.0
        self._sobel_x = torch.tensor(
            [[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]],
            dtype=torch.float32,
            device=self.device,
        )[None, None] * 0.125
        self._sobel_y = self._sobel_x.transpose(-1, -2).contiguous()

    def clear(self) -> None:
        self._cache.clear()
        if self.backend == "cuda":
            try:
                self.torch.cuda.empty_cache()
            except Exception:
                pass
        elif self.backend == "mps":
            try:
                self.torch.mps.empty_cache()
            except Exception:
                pass

    @staticmethod
    def _intrinsics(intrinsic: Any) -> tuple[float, float, float, float]:
        matrix = np.asarray(intrinsic.intrinsic_matrix, dtype=np.float64)
        return (
            float(matrix[0, 0]),
            float(matrix[1, 1]),
            float(matrix[0, 2]),
            float(matrix[1, 2]),
        )

    def _load_images(
        self, color_file: str, depth_file: str, config: dict[str, Any]
    ) -> tuple[Any, Any]:
        import cv2

        color = cv2.imread(str(color_file), cv2.IMREAD_UNCHANGED)
        depth = cv2.imread(str(depth_file), cv2.IMREAD_UNCHANGED)
        if color is None or depth is None:
            raise RuntimeError(
                f"无法为 GPU 里程计读取 RGB-D 图像: {color_file}, {depth_file}"
            )
        if depth.ndim != 2:
            raise RuntimeError(f"GPU 里程计要求单通道深度图: {depth_file}")
        if color.ndim == 2:
            intensity = color.astype(np.float32) / 255.0
        else:
            if color.shape[2] < 3:
                raise RuntimeError(f"GPU 里程计无法识别彩色图格式: {color_file}")
            bgr = color[..., :3].astype(np.float32) / 255.0
            intensity = (
                0.114 * bgr[..., 0]
                + 0.587 * bgr[..., 1]
                + 0.299 * bgr[..., 2]
            )

        depth_m = depth.astype(np.float32) / float(config["depth_scale"])
        valid = (
            (depth_m >= float(config.get("depth_min", 0.0)))
            & (depth_m < float(config["depth_max"]))
            & np.isfinite(depth_m)
        )
        # Legacy Open3D marks invalid depths as NaN before Gaussian filtering.
        # Keeping that representation is important: NaNs deliberately erode
        # depth boundaries and prevent unstable cross-surface matches.
        depth_m[~valid] = np.nan
        intensity_tensor = self.torch.from_numpy(
            np.ascontiguousarray(intensity)
        ).to(self.device)[None, None]
        depth_tensor = self.torch.from_numpy(np.ascontiguousarray(depth_m)).to(
            self.device
        )[None, None]
        return intensity_tensor, depth_tensor

    def _blur_intensity(self, image: Any) -> Any:
        padded = self.functional.pad(image, (1, 1, 1, 1), mode="replicate")
        return self.functional.conv2d(padded, self._gaussian_kernel)

    def _blur_depth(self, depth: Any) -> Any:
        padded = self.functional.pad(depth, (1, 1, 1, 1), mode="replicate")
        return self.functional.conv2d(padded, self._gaussian_kernel)

    def _downsample(self, image: Any) -> Any:
        return self.functional.avg_pool2d(image, kernel_size=2, stride=2)

    def _make_level(
        self,
        intensity: Any,
        depth: Any,
        intrinsic_values: tuple[float, float, float, float],
        divisor: int,
    ) -> _PyramidLevel:
        torch = self.torch
        height, width = depth.shape[-2:]
        level_intensity = intensity
        level_depth = depth

        fx, fy, cx, cy = intrinsic_values
        scale = 1.0 / divisor
        fx *= scale
        fy *= scale
        cx *= scale
        cy *= scale

        ys, xs = torch.meshgrid(
            torch.arange(height, device=self.device, dtype=torch.float32),
            torch.arange(width, device=self.device, dtype=torch.float32),
            indexing="ij",
        )
        z = level_depth[0, 0]
        vertex = torch.stack(
            ((xs - cx) * z / fx, (ys - cy) * z / fy, z), dim=-1
        )
        valid = z > 0
        flat_valid = valid.reshape(-1)

        padded_intensity = self.functional.pad(
            level_intensity, (1, 1, 1, 1), mode="replicate"
        )
        padded_depth = self.functional.pad(
            level_depth, (1, 1, 1, 1), mode="replicate"
        )
        intensity_dx = self.functional.conv2d(padded_intensity, self._sobel_x)
        intensity_dy = self.functional.conv2d(padded_intensity, self._sobel_y)
        depth_dx = torch.nan_to_num(
            self.functional.conv2d(padded_depth, self._sobel_x), nan=0.0
        )
        depth_dy = torch.nan_to_num(
            self.functional.conv2d(padded_depth, self._sobel_y), nan=0.0
        )

        return _PyramidLevel(
            intensity=level_intensity,
            depth=level_depth,
            intensity_dx=intensity_dx,
            intensity_dy=intensity_dy,
            depth_dx=depth_dx,
            depth_dy=depth_dy,
            source_points=vertex.reshape(-1, 3)[flat_valid],
            source_intensity=level_intensity.reshape(-1)[flat_valid],
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            width=width,
            height=height,
        )

    def _frame(
        self,
        color_file: str,
        depth_file: str,
        intrinsic: Any,
        config: dict[str, Any],
    ) -> _FramePyramid:
        intrinsic_values = self._intrinsics(intrinsic)
        key = (
            str(Path(color_file).resolve()),
            str(Path(depth_file).resolve()),
            intrinsic_values,
            float(config.get("depth_min", 0.0)),
            float(config["depth_max"]),
            float(config["depth_scale"]),
        )
        cached = self._cache.pop(key, None)
        if cached is not None:
            self._cache[key] = cached
            return cached

        intensity, depth = self._load_images(color_file, depth_file, config)
        intensity = self._blur_intensity(intensity)
        depth = self._blur_depth(depth)
        fine_to_coarse = [(intensity, depth)]
        for _ in range(1, len(self._DIVISORS)):
            previous_intensity, previous_depth = fine_to_coarse[-1]
            fine_to_coarse.append(
                (
                    self._downsample(self._blur_intensity(previous_intensity)),
                    self._downsample(previous_depth),
                )
            )
        frame = _FramePyramid(
            tuple(
                self._make_level(
                    level_intensity, level_depth, intrinsic_values, divisor
                )
                for (level_intensity, level_depth), divisor in zip(
                    reversed(fine_to_coarse), self._DIVISORS
                )
            )
        )
        self._cache[key] = frame
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return frame

    def _correspondences(
        self,
        source: _PyramidLevel,
        target: _PyramidLevel,
        transformation: Any,
        depth_diff_max: float,
    ) -> tuple[Any, Any, Any, Any, Any]:
        torch = self.torch
        points = source.source_points
        rotation = transformation[:3, :3]
        translation = transformation[:3, 3]
        transformed = points @ rotation.transpose(0, 1) + translation
        z = transformed[:, 2]
        safe_z = z.clamp_min(1e-6)
        projected_u = target.fx * transformed[:, 0] / safe_z + target.cx
        projected_v = target.fy * transformed[:, 1] / safe_z + target.cy
        pixel_u = torch.floor(projected_u + 0.5).to(torch.int64)
        pixel_v = torch.floor(projected_v + 0.5).to(torch.int64)
        inside = (
            (z > 0)
            & (pixel_u >= 0)
            & (pixel_u < target.width)
            & (pixel_v >= 0)
            & (pixel_v < target.height)
        )
        safe_u = pixel_u.clamp(0, target.width - 1)
        safe_v = pixel_v.clamp(0, target.height - 1)
        flat_index = safe_v * target.width + safe_u
        target_depth = target.depth.reshape(-1)[flat_index]
        valid = (
            inside
            & (target_depth > 0)
            & ((z - target_depth).abs() <= depth_diff_max)
        )
        return transformed, flat_index, target_depth, valid, z

    def _normalization_scales(
        self,
        source: _PyramidLevel,
        target: _PyramidLevel,
        transformation: Any,
        depth_diff_max: float,
    ) -> tuple[Any, Any]:
        torch = self.torch
        _, flat_index, _, valid, _ = self._correspondences(
            source, target, transformation, depth_diff_max
        )
        weights = valid.to(torch.float32)
        count = weights.sum().clamp_min(1.0)
        source_mean = (source.source_intensity * weights).sum() / count
        target_values = target.intensity.reshape(-1)[flat_index]
        target_mean = (target_values * weights).sum() / count
        source_scale = 0.5 / source_mean.clamp_min(1e-4)
        target_scale = 0.5 / target_mean.clamp_min(1e-4)
        return source_scale, target_scale

    def _linearize(
        self,
        source: _PyramidLevel,
        target: _PyramidLevel,
        transformation: Any,
        depth_diff_max: float,
        source_intensity_scale: Any,
        target_intensity_scale: Any,
    ) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
        torch = self.torch
        points = source.source_points
        if int(points.shape[0]) < 32:
            empty_j = torch.empty((0, 6), dtype=torch.float32, device=self.device)
            empty_r = torch.empty((0,), dtype=torch.float32, device=self.device)
            identity = torch.eye(6, dtype=torch.float32, device=self.device)
            zero = torch.zeros((), dtype=torch.float32, device=self.device)
            infinite = torch.full((), math.inf, dtype=torch.float32, device=self.device)
            return empty_j, empty_r, identity, identity, zero, zero, infinite

        p, flat_index, target_depth, valid, z = self._correspondences(
            source, target, transformation, depth_diff_max
        )
        target_intensity = target.intensity.reshape(-1)[flat_index]
        intensity_dx = target.intensity_dx.reshape(-1)[flat_index]
        intensity_dy = target.intensity_dy.reshape(-1)[flat_index]
        depth_dx = target.depth_dx.reshape(-1)[flat_index]
        depth_dy = target.depth_dy.reshape(-1)[flat_index]

        inv_z = z.clamp_min(1e-6).reciprocal()
        c0 = intensity_dx * target_intensity_scale * target.fx * inv_z
        c1 = intensity_dy * target_intensity_scale * target.fy * inv_z
        c2 = -(c0 * p[:, 0] + c1 * p[:, 1]) * inv_z
        image_gradient_3d = torch.stack((c0, c1, c2), dim=1)
        jacobian_photo = torch.cat(
            (torch.cross(p, image_gradient_3d, dim=1), image_gradient_3d),
            dim=1,
        )
        residual_photo = (
            target_intensity * target_intensity_scale
            - source.source_intensity * source_intensity_scale
        )

        d0 = depth_dx * target.fx * inv_z
        d1 = depth_dy * target.fy * inv_z
        d2 = -(d0 * p[:, 0] + d1 * p[:, 1]) * inv_z
        jacobian_depth = torch.stack(
            (
                -p[:, 2] * d1 + p[:, 1] * d2 - p[:, 1],
                p[:, 2] * d0 - p[:, 0] * d2 + p[:, 0],
                -p[:, 1] * d0 + p[:, 0] * d1,
                d0,
                d1,
                d2 - 1.0,
            ),
            dim=1,
        )
        residual_depth = target_depth - z

        weights = valid.to(torch.float32)
        photo_mix = math.sqrt(1.0 - self._LAMBDA_DEPTH)
        depth_mix = math.sqrt(self._LAMBDA_DEPTH)
        weighted_photo_j = torch.where(
            valid[:, None], jacobian_photo * photo_mix, 0.0
        )
        weighted_photo_r = torch.where(
            valid, residual_photo * photo_mix, 0.0
        )
        weighted_depth_j = torch.where(
            valid[:, None], jacobian_depth * depth_mix, 0.0
        )
        weighted_depth_r = torch.where(
            valid, residual_depth * depth_mix, 0.0
        )
        jacobian = torch.cat((weighted_photo_j, weighted_depth_j), dim=0)
        residual = torch.cat((weighted_photo_r, weighted_depth_r), dim=0)
        hessian = jacobian.transpose(0, 1) @ jacobian

        pixel_u = flat_index.remainder(target.width).to(torch.float32)
        pixel_v = torch.div(
            flat_index, target.width, rounding_mode="floor"
        ).to(torch.float32)
        qx = (pixel_u - target.cx) * target_depth / target.fx
        qy = (pixel_v - target.cy) * target_depth / target.fy
        qz = target_depth
        zeros = torch.zeros_like(qx)
        ones = torch.ones_like(qx)
        info_x = torch.stack((zeros, qz, -qy, ones, zeros, zeros), dim=1)
        info_y = torch.stack((-qz, zeros, qx, zeros, ones, zeros), dim=1)
        info_z = torch.stack((qy, -qx, zeros, zeros, zeros, ones), dim=1)
        info_x = torch.where(valid[:, None], info_x, 0.0)
        info_y = torch.where(valid[:, None], info_y, 0.0)
        info_z = torch.where(valid[:, None], info_z, 0.0)
        information = (
            info_x.transpose(0, 1) @ info_x
            + info_y.transpose(0, 1) @ info_y
            + info_z.transpose(0, 1) @ info_z
            + torch.eye(6, dtype=torch.float32, device=self.device)
        )
        count = valid.sum()
        fitness = count.to(torch.float32) / max(1, int(points.shape[0]))
        squared_error = torch.where(
            valid, residual_depth * residual_depth, 0.0
        ).sum()
        rmse = torch.where(
            count > 0,
            torch.sqrt(squared_error / count.clamp_min(1).to(torch.float32)),
            torch.full((), math.inf, dtype=torch.float32, device=self.device),
        )
        return jacobian, residual, hessian, information, count, fitness, rmse

    def _incremental_transform(self, delta: Any) -> Any:
        """Match Open3D's Rz * Ry * Rx Euler increment convention."""
        torch = self.torch
        rx, ry, rz = delta[:3].unbind()
        tx, ty, tz = delta[3:].unbind()
        zero = torch.zeros((), dtype=torch.float32, device=self.device)
        one = torch.ones((), dtype=torch.float32, device=self.device)
        sin_x, cos_x = torch.sin(rx), torch.cos(rx)
        sin_y, cos_y = torch.sin(ry), torch.cos(ry)
        sin_z, cos_z = torch.sin(rz), torch.cos(rz)
        rotation_x = torch.stack(
            (one, zero, zero, zero, cos_x, -sin_x, zero, sin_x, cos_x)
        ).reshape(3, 3)
        rotation_y = torch.stack(
            (cos_y, zero, sin_y, zero, one, zero, -sin_y, zero, cos_y)
        ).reshape(3, 3)
        rotation_z = torch.stack(
            (cos_z, -sin_z, zero, sin_z, cos_z, zero, zero, zero, one)
        ).reshape(3, 3)
        result = torch.eye(4, dtype=torch.float32, device=self.device)
        result[:3, :3] = rotation_z @ rotation_y @ rotation_x
        result[:3, 3] = torch.stack((tx, ty, tz))
        return result

    def estimate(
        self,
        source_color: str,
        source_depth: str,
        target_color: str,
        target_depth: str,
        intrinsic: Any,
        config: dict[str, Any],
        initial: np.ndarray | None = None,
    ) -> OdometryResult:
        torch = self.torch
        started = time.monotonic()
        with torch.inference_mode():
            source = self._frame(source_color, source_depth, intrinsic, config)
            target = self._frame(target_color, target_depth, intrinsic, config)
            initial_matrix = (
                np.eye(4, dtype=np.float32)
                if initial is None
                else np.asarray(initial, dtype=np.float32)
            )
            transformation = torch.as_tensor(
                initial_matrix, dtype=torch.float32, device=self.device
            ).clone()
            depth_diff_max = float(config["depth_diff_max"])
            source_scale, target_scale = self._normalization_scales(
                source.levels[-1],
                target.levels[-1],
                transformation,
                depth_diff_max,
            )

            information = torch.eye(6, dtype=torch.float32, device=self.device)
            for source_level, target_level, iterations in zip(
                source.levels, target.levels, self.iterations
            ):
                for _ in range(iterations):
                    (
                        jacobian,
                        residual,
                        hessian,
                        information,
                        _count,
                        _fitness,
                        _rmse,
                    ) = self._linearize(
                        source_level,
                        target_level,
                        transformation,
                        depth_diff_max,
                        source_scale,
                        target_scale,
                    )
                    if int(jacobian.shape[0]) == 0:
                        break
                    gradient = jacobian.transpose(0, 1) @ residual
                    diagonal = torch.diagonal(hessian).abs().clamp_min(1.0)
                    damped = hessian + torch.diag(diagonal * 1e-8)
                    delta = torch.linalg.solve(damped, -gradient)
                    delta = torch.nan_to_num(
                        delta, nan=0.0, posinf=0.0, neginf=0.0
                    )
                    transformation = (
                        self._incremental_transform(delta) @ transformation
                    )

            (
                _jacobian,
                _residual,
                _hessian,
                information,
                count,
                fitness,
                rmse,
            ) = self._linearize(
                source.levels[-1],
                target.levels[-1],
                transformation,
                depth_diff_max,
                source_scale,
                target_scale,
            )
            matrix = transformation.detach().cpu().numpy().astype(np.float64)
            information_matrix = information.detach().cpu().numpy().astype(np.float64)
            metrics = torch.stack(
                (count.to(torch.float32), fitness.to(torch.float32), rmse)
            ).detach().cpu().numpy()
            final_count = int(metrics[0])
            final_fitness = float(metrics[1])
            final_rmse = float(metrics[2])
            success = bool(
                final_count >= 64
                and final_fitness >= 0.03
                and math.isfinite(final_rmse)
                and final_rmse <= depth_diff_max
                and np.isfinite(matrix).all()
            )
            detail = (
                f"{final_count} 对应点，fitness={final_fitness:.3f}，"
                f"RMSE={final_rmse:.4f} m"
            )
            return OdometryResult(
                success=success,
                transformation=matrix,
                information=information_matrix,
                correspondences=final_count,
                fitness=final_fitness,
                inlier_rmse=final_rmse,
                elapsed_seconds=time.monotonic() - started,
                backend=self.backend,
                detail=detail,
            )


_SESSIONS: dict[str, TorchRGBDOdometry] = {}
_DISABLED: dict[str, str] = {}


def reset_gpu_odometry_state() -> None:
    for session in _SESSIONS.values():
        session.clear()
    _SESSIONS.clear()
    _DISABLED.clear()


def try_gpu_rgbd_odometry(
    source_color: str,
    source_depth: str,
    target_color: str,
    target_depth: str,
    intrinsic: Any,
    config: dict[str, Any],
    initial: np.ndarray | None = None,
) -> tuple[OdometryResult | None, str | None]:
    backend = str(config.get("compute_backend_resolved", "cpu"))
    if backend not in {"cuda", "mps"} or not config.get("compute_accelerated"):
        return None, None
    if backend in _DISABLED:
        return None, _DISABLED[backend]
    try:
        session = _SESSIONS.get(backend)
        if session is None:
            session = TorchRGBDOdometry(backend)
            _SESSIONS[backend] = session
        return (
            session.estimate(
                source_color,
                source_depth,
                target_color,
                target_depth,
                intrinsic,
                config,
                initial,
            ),
            None,
        )
    except Exception as exc:
        detail = f"{backend.upper()} 里程计运行失败: {exc}"
        _DISABLED[backend] = detail
        session = _SESSIONS.pop(backend, None)
        if session is not None:
            session.clear()
        return None, detail
