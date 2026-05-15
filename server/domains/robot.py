"""Robot + gripper domain: joint state tracking and task status."""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import WorkspaceLimits
from ..ros.bridge import RosBridge
from ..ros.launch import BRINGUP_COMMANDS, LaunchManager, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class MoveLimits:
    """Current move limits from workspace config."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    grid_spacing: float


@dataclass
class JointState:
    name: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=list)
    velocity: list[float] = field(default_factory=list)
    effort: list[float] = field(default_factory=list)


@dataclass
class RobotStatus:
    joints: JointState = field(default_factory=JointState)
    task_name: str | None = None
    task_status: TaskStatus = TaskStatus.IDLE


class RobotDomain:
    """Tracks robot state and delegates launch commands."""

    def __init__(
        self,
        bridge: RosBridge,
        launcher: LaunchManager,
        joint_states_topic: str = "/joint_states",
        workspace_limits: WorkspaceLimits | None = None,
        config_dir: Path | None = None,
        camera_info_topic: str | None = None,
        depth_topic: str | None = None,
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._status = RobotStatus()
        self._joint_topic = joint_states_topic
        self._subscribed = False
        self._commanded_pos: dict[str, float] | None = None
        self._ee_pos_ros: dict[str, float] | None = None
        self._config_dir = config_dir
        self._camera_info_topic = camera_info_topic
        self._depth_topic = depth_topic
        self._tf_frames: dict[str, tuple[str, np.ndarray]] = {}
        self._depth_image: np.ndarray | None = None
        self._cam_intrinsics: dict[str, float] | None = None
        self._move_limits = MoveLimits(
            x_min=workspace_limits.x_min if workspace_limits else -0.5,
            x_max=workspace_limits.x_max if workspace_limits else 0.5,
            y_min=workspace_limits.y_min if workspace_limits else -0.5,
            y_max=workspace_limits.y_max if workspace_limits else 0.5,
            z_min=workspace_limits.z_min if workspace_limits else 0.25,
            z_max=workspace_limits.z_max if workspace_limits else 0.55,
            grid_spacing=workspace_limits.grid_spacing if workspace_limits else 0.05,
        )

    @property
    def move_limits(self) -> dict[str, Any]:
        return {
            "x_min": self._move_limits.x_min,
            "x_max": self._move_limits.x_max,
            "y_min": self._move_limits.y_min,
            "y_max": self._move_limits.y_max,
            "z_min": self._move_limits.z_min,
            "z_max": self._move_limits.z_max,
            "grid_spacing": self._move_limits.grid_spacing,
        }

    @property
    def status(self) -> RobotStatus:
        active = self._launcher.active_action_task
        if active is not None:
            self._status.task_name = active.name
            self._status.task_status = active.status
        else:
            self._status.task_name = None
            self._status.task_status = TaskStatus.IDLE
        return self._status

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            self._joint_topic,
            "sensor_msgs/msg/JointState",
            self._on_joint_state,
            throttle_rate=100,
        )
        self._bridge.subscribe(
            "/ee_pose",
            "geometry_msgs/msg/PoseStamped",
            self._on_ee_pose,
            throttle_rate=200,
        )
        self._bridge.subscribe(
            "/tf",
            "tf2_msgs/msg/TFMessage",
            self._on_tf,
            throttle_rate=100,
        )
        self._bridge.subscribe(
            "/tf_static",
            "tf2_msgs/msg/TFMessage",
            self._on_tf,
        )
        if self._camera_info_topic:
            self._bridge.subscribe(
                self._camera_info_topic,
                "sensor_msgs/msg/CameraInfo",
                self._on_camera_info,
                throttle_rate=1000,
            )
        if self._depth_topic:
            self._bridge.subscribe(
                self._depth_topic,
                "sensor_msgs/msg/Image",
                self._on_depth,
                throttle_rate=2000,
            )
        self._subscribed = True

    def _on_ee_pose(self, msg: dict[str, Any]) -> None:
        pos = msg.get("pose", {}).get("position", {})
        if pos:
            self._ee_pos_ros = {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
            }

    def _on_tf(self, msg: dict[str, Any]) -> None:
        for t in msg.get("transforms", []):
            child = t.get("child_frame_id", "")
            if not child:
                continue
            parent = t["header"]["frame_id"]
            tr = t["transform"]["translation"]
            ro = t["transform"]["rotation"]
            mat = _quat_to_matrix(
                float(ro["x"]), float(ro["y"]), float(ro["z"]), float(ro["w"]),
                float(tr["x"]), float(tr["y"]), float(tr["z"]),
            )
            self._tf_frames[child] = (parent, mat)
        ee_mat = self._get_ee_matrix()
        if ee_mat is not None:
            self._ee_pos_ros = {
                "x": float(ee_mat[0, 3]),
                "y": float(ee_mat[1, 3]),
                "z": float(ee_mat[2, 3]),
            }

    def _on_camera_info(self, msg: dict[str, Any]) -> None:
        k = msg.get("k", [0.0] * 9)
        if len(k) >= 9:
            self._cam_intrinsics = {
                "fx": float(k[0]),
                "fy": float(k[4]),
                "ppx": float(k[2]),
                "ppy": float(k[5]),
            }

    def _on_depth(self, msg: dict[str, Any]) -> None:
        try:
            raw = base64.b64decode(msg["data"])
            h = int(msg["height"])
            w = int(msg["width"])
            self._depth_image = np.frombuffer(raw, dtype=np.uint16).reshape(h, w).copy()
        except Exception:
            pass

    def _on_joint_state(self, msg: dict[str, Any]) -> None:
        self._status.joints = JointState(
            name=msg.get("name", []),
            position=msg.get("position", []),
            velocity=msg.get("velocity", []),
            effort=[v if v is not None else 0.0 for v in msg.get("effort", [])],
        )

    async def start_task(
        self,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = await self._launcher.start(command, args)
        return {
            "name": task.name,
            "status": task.status.value,
            "pid": task.process.pid if task.process else None,
        }

    async def stop_task(self, name: str) -> dict[str, Any]:
        await self._launcher.stop(name)
        return {"name": name, "status": "stopped"}

    def get_ee_position(self) -> dict[str, float] | None:
        # Report only the measured /ee_pose. No fallback to the last
        # commanded target: a commanded goal is not the actual EE pose,
        # so when /ee_pose is unavailable we return None.
        return self._ee_pos_ros

    def get_status(self) -> dict[str, Any]:
        s = self.status
        bringup = self._launcher.bringup_task
        ee_pos = self._ee_pos_ros  # measured /ee_pose only; no commanded fallback
        return {
            "joints": {
                "name": s.joints.name,
                "position": s.joints.position,
                "velocity": s.joints.velocity,
                "effort": s.joints.effort,
            },
            "task": {
                "name": s.task_name,
                "status": s.task_status.value,
            },
            "bringup": {
                "name": bringup.name if bringup else None,
                "status": bringup.status.value if bringup else "idle",
            },
            "tasks": self._launcher.list_tasks(),
            "ee_position": ee_pos,
        }

    async def gripper_control(self, command: str) -> dict[str, Any]:
        result = await self._bridge.call_service(
            "/gripper_control",
            "cup_stack_interfaces/srv/GripperControl",
            {"command": command},
        )
        return result if result else {"success": False, "message": "Service call failed"}

    async def get_log(self, name: str, tail: int = 50) -> list[str]:
        return await self._launcher.get_log(name, tail)

    def _clamp_coord(self, value: float, min_val: float, max_val: float) -> float:
        """Clamp coordinate within limits."""
        return max(min_val, min(max_val, value))

    def _validate_target(
        self,
        x: float,
        y: float,
        z: float,
    ) -> tuple[float, float, float] | None:
        """Validate and clamp target within workspace limits."""
        if (x < self._move_limits.x_min or x > self._move_limits.x_max or
            y < self._move_limits.y_min or y > self._move_limits.y_max or
            z < self._move_limits.z_min or z > self._move_limits.z_max):
            logger.warning(
                "Target out of bounds: (%.3f, %.3f, %.3f), clamping to workspace",
                x, y, z,
            )
        return (
            self._clamp_coord(x, self._move_limits.x_min, self._move_limits.x_max),
            self._clamp_coord(y, self._move_limits.y_min, self._move_limits.y_max),
            self._clamp_coord(z, self._move_limits.z_min, self._move_limits.z_max),
        )

    async def move_to(
        self,
        x: float,
        y: float,
        z: float,
        mode: str = "absolute",
    ) -> dict[str, Any]:
        """Move robot end-effector via Doosan /motion/move_line (no MoveItPy)."""
        if mode == "relative":
            # Relative delta — no clamping; orientation delta is zero
            req = {
                "pos": [x * 1000.0, y * 1000.0, z * 1000.0, 0.0, 0.0, 0.0],
                "vel": [50.0, 30.0],
                "acc": [100.0, 60.0],
                "time": 0.0,
                "radius": 0.0,
                "ref": 0,
                "mode": 1,         # DR_MV_MOD_REL
                "blend_type": 0,
                "sync_type": 0,    # SYNC (blocking)
            }
        else:
            # Absolute Cartesian move; keep tool pointing down (ry=180°)
            clamped = self._validate_target(x, y, z)
            target_x, target_y, target_z = clamped
            req = {
                "pos": [target_x * 1000.0, target_y * 1000.0, target_z * 1000.0, 0.0, 180.0, 0.0],
                "vel": [50.0, 30.0],
                "acc": [100.0, 60.0],
                "time": 0.0,
                "radius": 0.0,
                "ref": 0,
                "mode": 0,         # DR_MV_MOD_ABS
                "blend_type": 0,
                "sync_type": 0,
            }

        try:
            result = await self._bridge.call_service(
                "/motion/move_line",
                "dsr_msgs2/srv/MoveLine",
                req,
                timeout=30.0,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"Move failed: {exc}") from exc

        ok = bool(result.get("success", False)) if result else False
        if ok and mode != "relative":
            self._commanded_pos = {"x": target_x, "y": target_y, "z": target_z}

        return {
            "success": ok,
            "message": "Moved" if ok else "Move command failed",
            "position": self._commanded_pos,
        }

    def _get_ee_matrix(self) -> np.ndarray | None:
        target, base = "link_6", "base_link"
        chain: list[np.ndarray] = []
        current = target
        for _ in range(20):
            if current == base:
                break
            entry = self._tf_frames.get(current)
            if entry is None:
                return None
            parent, mat = entry
            chain.append(mat)
            current = parent
        else:
            return None
        if current != base:
            return None
        result = np.eye(4)
        for m in reversed(chain):
            result = result @ m
        return result

    def pixel_to_world(
        self, px: int, py: int
    ) -> dict[str, Any]:
        ee_matrix = self._get_ee_matrix()
        if ee_matrix is None:
            raise ValueError("TF not available (bringup not running?)")

        if self._cam_intrinsics is None:
            raise ValueError("Camera intrinsics not received")

        if self._depth_image is None:
            raise ValueError("Depth image not received")

        if self._config_dir is None:
            raise ValueError("config_dir not configured")

        calib_file = Path(self._config_dir) / "T_gripper2camera.npy"
        if not calib_file.exists():
            raise ValueError(f"Calibration file not found: {calib_file}")

        gripper_to_camera = np.load(str(calib_file)).astype(float)
        gripper_to_camera[:3, 3] /= 1000.0

        h, w = self._depth_image.shape
        z_raw = _find_depth(self._depth_image, px, py, h, w)
        if z_raw is None:
            raise ValueError(f"No valid depth near pixel ({px}, {py})")

        fx = self._cam_intrinsics["fx"]
        fy = self._cam_intrinsics["fy"]
        ppx = self._cam_intrinsics["ppx"]
        ppy = self._cam_intrinsics["ppy"]
        z_m = z_raw / 1000.0
        cam_point = np.array(
            [(px - ppx) * z_m / fx, (py - ppy) * z_m / fy, z_m, 1.0],
            dtype=float,
        )

        base_point = (ee_matrix @ gripper_to_camera) @ cam_point
        return {
            "x": float(base_point[0]),
            "y": float(base_point[1]),
            "z": float(base_point[2]),
            "depth_mm": int(z_raw),
            "pixel_x": px,
            "pixel_y": py,
        }


def _quat_to_matrix(
    qx: float, qy: float, qz: float, qw: float,
    tx: float, ty: float, tz: float,
) -> np.ndarray:
    mat = np.eye(4)
    mat[0, 0] = 1 - 2 * (qy * qy + qz * qz)
    mat[0, 1] = 2 * (qx * qy - qz * qw)
    mat[0, 2] = 2 * (qx * qz + qy * qw)
    mat[1, 0] = 2 * (qx * qy + qz * qw)
    mat[1, 1] = 1 - 2 * (qx * qx + qz * qz)
    mat[1, 2] = 2 * (qy * qz - qx * qw)
    mat[2, 0] = 2 * (qx * qz - qy * qw)
    mat[2, 1] = 2 * (qy * qz + qx * qw)
    mat[2, 2] = 1 - 2 * (qx * qx + qy * qy)
    mat[0, 3] = tx
    mat[1, 3] = ty
    mat[2, 3] = tz
    return mat


def _find_depth(img: np.ndarray, px: int, py: int, h: int, w: int) -> int | None:
    if not (0 <= px < w and 0 <= py < h):
        return None
    z = int(img[py, px])
    if z > 0:
        return z
    radius = 15
    x0, x1 = max(0, px - radius), min(w, px + radius + 1)
    y0, y1 = max(0, py - radius), min(h, py + radius + 1)
    patch = img[y0:y1, x0:x1]
    valid = patch[patch > 0]
    if valid.size == 0:
        return None
    return int(np.percentile(valid, 25))
