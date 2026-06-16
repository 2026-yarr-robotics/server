"""Robot + gripper domain: joint state tracking and task status."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import RobotHome, WorkspaceLimits
from ..ros.bridge import RosBridge
from ..ros.launch import AGENT_COMMAND, BRINGUP_COMMANDS, LaunchManager, TaskStatus

logger = logging.getLogger(__name__)

# TF/ee_pose older than this is considered stale; get_ee_position returns None.
EE_POSE_STALE_SEC = 1.0

# Gripper width is published by gripper_node at ~5 Hz; older than this is
# considered stale (hardware down / gripper_node not running) and reported
# as None so the dashboard shows "—" instead of a frozen value.
GRIPPER_WIDTH_STALE_SEC = 2.0

# Lazy skill_api lifecycle: started on the first pick via the bringup agent and
# left running (no stop-after). MoveItPy init in skill_api_node is slow, so
# allow a generous readiness window.
SKILL_API_COMMAND = "skill_api"
SKILL_API_READY_TIMEOUT = 90.0   # seconds to wait for /status ready=true
SKILL_API_POLL_INTERVAL = 2.0    # seconds between readiness probes

# 3-2-1 pyramid geometry (matches cup_stack.skills.config.SkillStackConfig).
# Slot layout — lateral offset (m) and vertical layer (0=bottom, 2=top).
PYRAMID_CUP_SPACING = 0.078
PYRAMID_LAYER_HEIGHT = 0.093
PYRAMID_PLACE_Z_BASE = 0.318
DEFAULT_PYRAMID_DEGREE = 90.0
DEFAULT_PYRAMID_PICK_Z = 0.313  # SkillStackConfig.pick_z_base
# Per-cup nest increment (m) for the unstack destination column. 12.7 mm is the
# working cup geometry used by every launch invocation and the interactive
# cup_{pyramid,unstack}_select nodes (`nest_inc:=0.0127`); the 0.012 fallback
# baked into the plain nodes / skill_api.launch.py is always overridden at launch.
DEFAULT_NEST_INC = 0.0127
PYRAMID_SLOT_OFFSETS: dict[str, tuple[float, int]] = {
    "1l": (-PYRAMID_CUP_SPACING,      0),
    "1m": (0.0,                       0),
    "1r": ( PYRAMID_CUP_SPACING,      0),
    "2l": (-PYRAMID_CUP_SPACING / 2,  1),
    "2r": ( PYRAMID_CUP_SPACING / 2,  1),
    "3m": (0.0,                       2),
}

# Full 3-2-1 teardown order, top → bottom (the only valid unstack order — a
# lower cup can only be lifted once the ones resting on it are gone). The
# destination column height (``nested``) is the 1-based index in this list, so
# cup N nests on top of the previous one. Mirrors ``script/unstack.sh``'s
# ``SLOTS=(3m 2r 2l 1r 1m 1l)``; :meth:`RobotDomain.unstack_all_skill` walks it.
UNSTACK_SEQUENCE: tuple[str, ...] = ("3m", "2r", "2l", "1r", "1m", "1l")

# Default destination nest XY for the full teardown (base_link, m). Matches the
# ``DEST_X``/``DEST_Y`` defaults in ``script/unstack.sh``.
DEFAULT_UNSTACK_DEST_X = 0.400
DEFAULT_UNSTACK_DEST_Y = 0.100

# Yaw twist (deg) applied to the unstack grip orientation. Set to 90.0 to hold
# the wrist (J6) at the joint-HOME yaw ([0,0,90,0,90,90]) so it never swings
# ~90° between HOME and a pick/place (== the skill package's PICK_ORI,
# make_twist_orientation(90)). Forwarded to skill_api /skill/pyramid_step;
# build (pyramid_skill) is unaffected (always 0.0).
#
# Default 0.0 (natural down grip): the wrist-swing fix is instead handled by
# (1) skipping the per-cup HOME return (one HOME per teardown, not six) and
# (3) the OMPL nearest-wrist constraint (runtime.WRIST_NEAREST_TOL) preventing
# 180° wraps — no grip-geometry change, so no finger/cup collision risk. Flip
# to 90.0 to additionally null the first/last genuine HOME<->grip swing.
UNSTACK_GRIP_TWIST_DEG = 0.0

# ── Safety-stop ("yellow light") auto-recovery ───────────────────────────────
# A velocity/acceleration-limit violation drops the Doosan controller into a
# safety-stopped state (the amber/yellow status lamp). Unlike a red EMERGENCY
# stop (physical button), this is software-recoverable: set_robot_control
# returns the SW-recoverable safety states to STANDBY *without* restarting
# bringup, so the arm keeps its pose and the (separate Modbus) OnRobot gripper
# keeps its grip — the interrupted motion can resume in place.
#
# robot_state enum (dsr_msgs2/srv/GetRobotState).
ROBOT_STATE_INITIALIZING = 0
ROBOT_STATE_STANDBY = 1
ROBOT_STATE_MOVING = 2
ROBOT_STATE_SAFE_OFF = 3
ROBOT_STATE_TEACHING = 4
ROBOT_STATE_SAFE_STOP = 5        # accel/vel-limit "yellow light"
ROBOT_STATE_EMERGENCY_STOP = 6   # red E-stop — human must release the button
ROBOT_STATE_HOMMING = 7
ROBOT_STATE_RECOVERY = 8
ROBOT_STATE_SAFE_STOP2 = 9       # collision-class — needs RECOVERY flow/reboot
ROBOT_STATE_SAFE_OFF2 = 10
ROBOT_STATE_NOT_READY = 15
_ROBOT_STATE_NAMES = {
    0: "INITIALIZING", 1: "STANDBY", 2: "MOVING", 3: "SAFE_OFF",
    4: "TEACHING", 5: "SAFE_STOP", 6: "EMERGENCY_STOP", 7: "HOMMING",
    8: "RECOVERY", 9: "SAFE_STOP2", 10: "SAFE_OFF2", 15: "NOT_READY",
}
# States that mean "not stopped" — nothing to recover.
_ROBOT_STATE_RUNNING = frozenset({
    ROBOT_STATE_INITIALIZING, ROBOT_STATE_STANDBY,
    ROBOT_STATE_MOVING, ROBOT_STATE_HOMMING,
})
# set_robot_control command per SW-recoverable safety state
# (dsr_msgs2/srv/SetRobotControl): 2=RESET_SAFET_STOP, 3=RESET_SAFET_OFF.
_RECOVER_CONTROL = {
    ROBOT_STATE_SAFE_STOP: 2,
    ROBOT_STATE_SAFE_OFF: 3,
}
ROBOT_MODE_AUTONOMOUS = 1        # dsr_msgs2/srv/SetRobotMode
RECOVER_POLL_S = 0.2
RECOVER_TIMEOUT_S = 8.0
# move_to: clear one safety stop and retry at reduced speed so the same
# acceleration limit is not immediately re-tripped.
MOVE_RECOVER_RETRIES = 1
MOVE_RECOVER_VEL_SCALE = 0.5
MOVE_VEL = [250.0, 60.0]   # linear mm/s, angular deg/s
MOVE_ACC = [400.0, 120.0]  # linear mm/s^2, angular deg/s^2


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
        robot_home: RobotHome | None = None,
        config_dir: Path | None = None,
        camera_info_topic: str | None = None,
        depth_topic: str | None = None,
        skill_api_url: str = "http://localhost:8765",
        pyramid_state_path: Path | None = None,
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._skill_api_url = skill_api_url.rstrip("/")
        self._skill_api_lock = asyncio.Lock()
        # Set by stop_all() to make in-flight server-side multi-step loops
        # (unstack_all_skill) bail between steps; reset when such a loop starts.
        self._stop_requested = False
        self._status = RobotStatus()
        self._joint_topic = joint_states_topic
        self._subscribed = False
        self._commanded_pos: dict[str, float] | None = None
        self._ee_pos_ros: dict[str, float] | None = None
        self._ee_pos_ros_ts: float | None = None
        self._gripper_mm: float | None = None
        self._gripper_mm_ts: float | None = None
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
        home = robot_home or RobotHome()
        self._robot_home = {"x": float(home.x), "y": float(home.y)}

        # 3-2-1 pyramid config: center XY (None ⇒ initialized from configured
        # HOME XY on first read), degree (yaw, +x axis CCW), pick gripper Z, and the
        # cached 6-slot absolute place coordinates.
        self._pyramid_center: dict[str, float] | None = None
        self._pyramid_degree: float = DEFAULT_PYRAMID_DEGREE
        self._pyramid_pick_z: float = DEFAULT_PYRAMID_PICK_Z
        self._pyramid_slots: dict[str, dict[str, float]] = {}
        # Persist pyramid config (center/degree/pick_z) across restarts.
        self._pyramid_state_path = pyramid_state_path
        self._load_pyramid_config()

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
        self._bridge.subscribe(
            "/gripper/width",
            "std_msgs/msg/Float32",
            self._on_gripper_width,
            throttle_rate=100,
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
        if pos and {"x", "y", "z"} <= pos.keys():
            self._ee_pos_ros = {
                "x": float(pos["x"]),
                "y": float(pos["y"]),
                "z": float(pos["z"]),
            }
            self._ee_pos_ros_ts = time.monotonic()

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
            self._ee_pos_ros_ts = time.monotonic()

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

    def _on_gripper_width(self, msg: dict[str, Any]) -> None:
        data = msg.get("data")
        if isinstance(data, (int, float)):
            self._gripper_mm = float(data)
            self._gripper_mm_ts = time.monotonic()

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
        ros_stop_success = await self._stop_motion()
        await self._launcher.stop(name)
        return {"name": name, "status": "stopped", "ros_stop_success": ros_stop_success}

    async def stop_all(self, home: bool = True) -> dict[str, Any]:
        """실행 중인 무엇이든 즉시 멈추고 팔을 HOME 으로 복귀시킨다.

        통합 정지(패닉/abort) 진입점:

        1. 서버측 다단계 루프(:meth:`unstack_all_skill`)가 다음 스텝 전에
           빠져나오도록 ``_stop_requested`` 를 세운다.
        2. DRCF ``MoveStop`` 퀵스탑을 보내 물리적으로 즉시 정지한다.
        3. 실행 중인 action task 프로세스(fallen/outlier/agent)가 있으면 kill.
           (프로세스 사망 = 그 안의 MoveItPy servoj 스트리밍도 끊김.)
        4. skill_api_node 의 ``POST /stop`` 을 호출해 진행 중인 skill 을
           인터럽트하고 충돌 회피 HOME 복귀까지 시킨다. skill_api 가 떠 있지
           않으면(=action task 인터럽트 케이스) HOME 은 생략됨으로 보고한다.

        skill 의 모션은 상시 노드인 skill_api_node 안에서 MoveItPy 로
        스트리밍되므로 서버 퀵스탑만으로는 신뢰성 있게 끊기지 않는다 —
        실제 인터럽트는 (4)의 skill_api ``/stop`` 이 담당한다.
        """
        self._stop_requested = True
        ros_stop = await self._stop_motion()

        # Kill every process that would keep driving motion past the interrupt:
        #   - an active action task (fallen/outlier recovery), and
        #   - the agent LLM loop. The agent is excluded from active_action_task
        #     by design, but it keeps POSTing skills, so it must be stopped too
        #     or it re-issues motion right after the interrupt.
        running = {
            t["name"] for t in self._launcher.list_tasks()
            if t.get("status") == "running"
        }
        to_kill: list[str] = []
        active = self._launcher.active_action_task
        if active is not None:
            to_kill.append(active.name)
        if AGENT_COMMAND in running and AGENT_COMMAND not in to_kill:
            to_kill.append(AGENT_COMMAND)

        killed: list[str] = []
        for name in to_kill:
            try:
                await self._launcher.stop(name)
                killed.append(name)
            except Exception as exc:
                logger.warning("stop_all: task '%s' stop failed: %s", name, exc)

        skill_stop = await self._skill_api_stop(home=home)
        homed = bool(skill_stop.get("homed")) if skill_stop else False
        interrupted = (
            bool(skill_stop.get("interrupted")) if skill_stop else ros_stop
        )

        parts: list[str] = []
        if killed:
            parts.append("killed " + ", ".join(killed))
        if skill_stop:
            parts.append(skill_stop.get("detail") or "skill interrupted")
        elif killed:
            parts.append("skill_api down; HOME skipped")
        else:
            parts.append("nothing running; quick-stop sent")

        return {
            "success": True,
            "ros_stop": ros_stop,
            "interrupted": interrupted,
            "killed_tasks": killed,
            "homed": homed,
            "detail": "; ".join(parts),
        }

    async def _skill_api_stop(self, home: bool = True) -> dict[str, Any] | None:
        """skill_api_node 의 ``POST /stop`` 을 호출 (인터럽트 + HOME 복귀).

        skill_api JSON 응답을 반환하고, skill_api 가 안 떠 있으면(=action task
        인터럽트 케이스로 skill_api 가 정지된 상태) ``None`` 을 반환한다. HOME
        이동이 ~10–20s 걸릴 수 있어 넉넉한 타임아웃을 둔다.
        """
        url = (
            f"{self._skill_api_url}/stop"
            f"?home={'true' if home else 'false'}"
        )
        req = urllib.request.Request(url, data=b"", method="POST")
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any] | None:
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.URLError as exc:
                logger.info("skill_api /stop unreachable: %s", exc)
                return None
            except Exception as exc:  # noqa: BLE001 - stop must not raise
                logger.warning("skill_api /stop error: %s", exc)
                return None

        return await loop.run_in_executor(None, _call)

    async def move_home(self) -> dict[str, Any]:
        """팔을 조인트 HOME 으로 복귀시킨다 (인터럽트 없는 단순 HOME).

        skill_api_node 의 ``POST /home`` 을 호출 — place 후 매번 쓰는 것과 같은
        ``try_move_home``. ``/stop`` 과 달리 진행 중 skill 인터럽트나 퀵스탑이
        없고, skill 이 돌고 있으면 skill_api 가 거부한다. 에이전트의 pick 실패
        HOME 복귀용(돌고 있는 skill 없음·컵 안 든 상태)이다.
        """
        res = await self._skill_api_home()
        if res is None:
            return {
                "success": False, "homed": False,
                "detail": "skill_api unreachable; HOME skipped",
            }
        return {
            "success": bool(res.get("success")),
            "homed": bool(res.get("homed")),
            "detail": res.get("detail") or "",
        }

    async def _skill_api_home(self) -> dict[str, Any] | None:
        """skill_api_node 의 ``POST /home`` 호출 (인터럽트 없는 단순 HOME).

        skill_api JSON 응답을 반환하고, skill_api 가 안 떠 있으면 ``None`` 을
        반환한다. HOME 이동이 ~10–20s 걸릴 수 있어 넉넉한 타임아웃을 둔다.
        """
        url = f"{self._skill_api_url}/home"
        req = urllib.request.Request(url, data=b"", method="POST")
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any] | None:
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.URLError as exc:
                logger.info("skill_api /home unreachable: %s", exc)
                return None
            except Exception as exc:  # noqa: BLE001 - home must not raise
                logger.warning("skill_api /home error: %s", exc)
                return None

        return await loop.run_in_executor(None, _call)

    async def start_fallen_cup_recovery(
        self,
        mode: str = "drop",
        multi_cup: bool = False,
        dry_run: bool = False,
        sim: bool = False,
        stand_cup_margin_m: float | None = None,
        place_safe_z_min: float | None = None,
        place_cup_tilt_deg: float | None = None,
        place_plus_y_cup_tilt_deg: float | None = None,
    ) -> dict[str, Any]:
        """넘어진 컵 세우기 태스크(fallen_cup_recovery launch)를 시작한다.

        stand_fallen_cup 노드는 MoveItPy + dsr_moveit_controller 를 사용하므로
        장기 실행 중인 skill_api(역시 MoveItPy 기반)와 컨트롤러 경합이 생긴다.
        시작 전에 skill_api 를 best-effort 로 정지시킨다 (다음 pick/pyramid
        호출 시 lazy 재시작됨).

        ``stand_cup_margin_m``/``place_safe_z_min`` 은 place 단계의 Z 안전
        파라미터 — 생략하면 launch 파일의 안전 기본값(+0.05 / 0.15)을 쓴다.
        """
        try:
            await self._launcher.stop(SKILL_API_COMMAND)
        except Exception as exc:
            logger.warning("skill_api stop before fallen_cup_recovery failed: %s", exc)

        args = {
            "mode": mode,
            "multi_cup": str(multi_cup).lower(),
            "dry_run": str(dry_run).lower(),
            "sim": str(sim).lower(),
        }
        if stand_cup_margin_m is not None:
            args["stand_cup_margin_m"] = str(stand_cup_margin_m)
        if place_safe_z_min is not None:
            args["place_safe_z_min"] = str(place_safe_z_min)
        if place_cup_tilt_deg is not None:
            args["place_cup_tilt_deg"] = str(place_cup_tilt_deg)
        if place_plus_y_cup_tilt_deg is not None:
            args["place_plus_y_cup_tilt_deg"] = str(place_plus_y_cup_tilt_deg)
        return await self.start_task("fallen_cup_recovery", args)

    async def start_outlier_cup_recovery(
        self,
        mode: str = "drop",
        dry_run: bool = False,
        sim: bool = False,
    ) -> dict[str, Any]:
        """outlier 컵 복구 오케스트레이터 태스크(outlier_cup_recovery launch)를 시작한다.

        fallen cup 을 전부 세운 뒤 mouth-up cup 을 전부 뒤집는 상위 집합 스킬.
        fallen-only 인 ``start_fallen_cup_recovery`` 와 같은 MoveItPy +
        dsr_moveit_controller 를 쓰므로 skill_api 와 컨트롤러 경합이 생긴다 —
        시작 전에 skill_api 를 best-effort 로 정지시킨다 (다음 pick/pyramid
        호출 시 lazy 재시작됨).

        ``multi_cup`` 은 오케스트레이터가 강제 ON 이라 인자로 받지 않는다.
        """
        try:
            await self._launcher.stop(SKILL_API_COMMAND)
        except Exception as exc:
            logger.warning("skill_api stop before outlier_cup_recovery failed: %s", exc)

        args = {
            "mode": mode,
            "dry_run": str(dry_run).lower(),
            "sim": str(sim).lower(),
        }
        return await self.start_task("outlier_cup_recovery", args)

    async def _stop_motion(self) -> bool:
        """Best-effort: Doosan 컨트롤러에 퀵스탑(MoveStop) 명령을 보낸다.

        dsr_controller2 가 advertise 하는 실제 서비스는
        ``/dsr01/motion/move_stop`` (``dsr_msgs2/srv/MoveStop``)다. ``stop_mode``
        ``1`` = DR_QSTOP(quick stop, category 2). 이전에는 존재하지 않는
        ``/motion/stop_motion`` + ``StopMotion`` 타입을 호출해 항상 조용히
        실패했었다.
        """
        if not self._bridge.connected:
            return False
        try:
            res = await self._bridge.call_service(
                "/dsr01/motion/move_stop",
                "dsr_msgs2/srv/MoveStop",
                {"stop_mode": 1},
                timeout=2.0,
            )
            if isinstance(res, dict) and "success" in res:
                return bool(res["success"])
            return True
        except Exception as exc:
            logger.warning("move_stop service call failed: %s", exc)
            return False

    async def _get_robot_state(self) -> int | None:
        """Current Doosan robot_state enum, or None if unavailable."""
        if not self._bridge.connected:
            return None
        try:
            res = await self._bridge.call_service(
                "/dsr01/system/get_robot_state",
                "dsr_msgs2/srv/GetRobotState",
                {},
                timeout=3.0,
            )
        except Exception as exc:
            logger.warning("get_robot_state service call failed: %s", exc)
            return None
        if not res:
            return None
        try:
            return int(res.get("robot_state"))
        except (TypeError, ValueError):
            return None

    async def recover_safe_stop(self) -> dict[str, Any]:
        """Clear a Doosan safety stop (the accel/vel-limit "yellow light")
        in place, without restarting bringup.

        Diagnoses ``robot_state`` and, for the SW-recoverable safety states
        (SAFE_STOP / SAFE_OFF), issues ``set_robot_control`` to return to
        STANDBY, then ``set_robot_mode`` AUTONOMOUS, polling until STANDBY.
        The arm holds its pose and the OnRobot gripper (separate Modbus link)
        holds its grip across the reset, so the caller can resume the
        interrupted motion in place.

        Not auto-cleared (caller must escalate to a bringup restart / human):
        EMERGENCY_STOP (red, physical button) and the collision-class
        SAFE_STOP2 / SAFE_OFF2 / RECOVERY states.

        Returns ``{recovered, from_state, from_state_name, to_state,
        to_state_name, detail}``. ``recovered`` is True when the robot is at
        (or was never out of) STANDBY.
        """
        state = await self._get_robot_state()
        info: dict[str, Any] = {
            "recovered": False,
            "from_state": state,
            "from_state_name": _ROBOT_STATE_NAMES.get(state, str(state)),
            "to_state": state,
            "to_state_name": _ROBOT_STATE_NAMES.get(state, str(state)),
            "detail": "",
        }
        if state is None:
            info["detail"] = "robot state unavailable (rosbridge / get_robot_state)"
            return info
        if state in _ROBOT_STATE_RUNNING:
            info["recovered"] = True
            info["detail"] = "no safety stop active"
            return info
        if state == ROBOT_STATE_EMERGENCY_STOP:
            info["detail"] = (
                "EMERGENCY_STOP (red): release the physical E-stop button manually"
            )
            return info
        control = _RECOVER_CONTROL.get(state)
        if control is None:
            info["detail"] = (
                f"state {info['from_state_name']} is not SW-auto-recoverable; "
                "restart bringup or run the RECOVERY flow"
            )
            return info

        # Reset the safety stop, then ensure autonomous mode.
        try:
            await self._bridge.call_service(
                "/dsr01/system/set_robot_control",
                "dsr_msgs2/srv/SetRobotControl",
                {"robot_control": control},
                timeout=5.0,
            )
            await self._bridge.call_service(
                "/dsr01/system/set_robot_mode",
                "dsr_msgs2/srv/SetRobotMode",
                {"robot_mode": ROBOT_MODE_AUTONOMOUS},
                timeout=5.0,
            )
        except Exception as exc:
            info["detail"] = f"recovery service call failed: {exc}"
            return info

        # Poll until the controller settles back to STANDBY.
        deadline = time.monotonic() + RECOVER_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(RECOVER_POLL_S)
            cur = await self._get_robot_state()
            if cur is not None:
                info["to_state"] = cur
                info["to_state_name"] = _ROBOT_STATE_NAMES.get(cur, str(cur))
            if cur == ROBOT_STATE_STANDBY:
                info["recovered"] = True
                info["detail"] = (
                    f"reset from {info['from_state_name']} to STANDBY"
                )
                return info
        info["detail"] = (
            f"reset issued but did not reach STANDBY within "
            f"{RECOVER_TIMEOUT_S:.0f}s (last={info['to_state_name']})"
        )
        return info

    async def _run_skill_call(self, call: Any) -> dict[str, Any]:
        """Run a blocking skill_api POST in the executor, with safety-stop recovery.

        On a skill error (``RuntimeError`` — an HTTP error from skill_api_node,
        which is how a mid-skill safety stop surfaces), best-effort clear a
        safety stop (the accel/vel-limit "yellow light") so the arm returns to
        STANDBY and the next action is not blocked, then re-raise. The skill is
        **not** re-run: mid-skill state is unknown (the cup may be half-picked),
        so the caller / LLM loop observes the failure and replans.
        ``ConnectionError`` (skill_api unreachable) propagates untouched — it is
        not a safety stop and rosbridge is likely down too.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, call)
        except RuntimeError as exc:
            rec = await self.recover_safe_stop()
            if rec["recovered"] and rec["from_state"] in _RECOVER_CONTROL:
                logger.warning(
                    "skill tripped %s; cleared to STANDBY (skill not re-run)",
                    rec["from_state_name"],
                )
                raise RuntimeError(
                    f"{exc} [safety stop cleared: "
                    f"{rec['from_state_name']}->STANDBY]"
                ) from exc
            raise

    def get_ee_position(self) -> dict[str, float] | None:
        ts = self._ee_pos_ros_ts
        if (
            self._ee_pos_ros is not None
            and ts is not None
            and (time.monotonic() - ts) <= EE_POSE_STALE_SEC
        ):
            return self._ee_pos_ros
        return None

    def get_gripper_mm(self) -> float | None:
        ts = self._gripper_mm_ts
        if (
            self._gripper_mm is not None
            and ts is not None
            and (time.monotonic() - ts) <= GRIPPER_WIDTH_STALE_SEC
        ):
            return self._gripper_mm
        return None

    def get_status(self) -> dict[str, Any]:
        s = self.status
        # Snapshot once: _on_joint_state (rosbridge thread) may swap
        # s.joints between field reads, producing mismatched arrays.
        j = s.joints
        bringup = self._launcher.bringup_task
        ee_pos = self.get_ee_position()
        return {
            "joints": {
                "name": j.name,
                "position": j.position,
                "velocity": j.velocity,
                "effort": j.effort,
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
            "gripper": {"width_mm": self.get_gripper_mm()},
        }

    async def gripper_control(self, command: str) -> dict[str, Any]:
        result = await self._bridge.call_service(
            "/gripper_control",
            "cup_stack_interfaces/srv/GripperControl",
            {"command": command},
        )
        return result if result else {"success": False, "message": "Service call failed"}

    async def run_agent(self, text: str) -> dict[str, Any]:
        """Run the cup_stack_agent LLM loop for a natural-language command.

        A user command (e.g. "3단 쌓아줘") launches the agent's own
        ``start.sh --real-api`` as a local subprocess, passing the text via the
        ``USER_COMMAND`` env var (start.sh forwards it as the aggregator node's
        ``user_command`` ROS parameter). Re-sending a command restarts the loop.
        """
        await self._launcher.start(AGENT_COMMAND, {"user_command": text})
        return {"success": True, "message": f"agent started: {text}"}

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

    def _build_move_req(
        self,
        x: float,
        y: float,
        z: float,
        mode: str,
        vel_scale: float,
    ) -> tuple[dict[str, Any], tuple[float, float, float] | None]:
        """Build a MoveLine request.

        ``vel_scale`` < 1 throttles both velocity and acceleration so a
        post-recovery retry does not re-trip the acceleration limit that
        caused the safety stop. Returns ``(request, clamped_xyz)`` where the
        clamped tuple is None for relative moves.
        """
        vel = [v * vel_scale for v in MOVE_VEL]
        acc = [a * vel_scale for a in MOVE_ACC]
        if mode == "relative":
            return {
                "pos": [x * 1000.0, y * 1000.0, z * 1000.0, 0.0, 0.0, 0.0],
                "vel": vel, "acc": acc, "time": 0.0, "radius": 0.0,
                "ref": 0, "mode": 1, "blend_type": 0, "sync_type": 0,
            }, None
        clamped = self._validate_target(x, y, z)
        tx, ty, tz = clamped
        return {
            "pos": [tx * 1000.0, ty * 1000.0, tz * 1000.0, 0.0, 180.0, 0.0],
            "vel": vel, "acc": acc, "time": 0.0, "radius": 0.0,
            "ref": 0, "mode": 0, "blend_type": 0, "sync_type": 0,
        }, clamped

    async def move_to(
        self,
        x: float,
        y: float,
        z: float,
        mode: str = "absolute",
    ) -> dict[str, Any]:
        """Move robot end-effector via Doosan /motion/move_line.

        If the move trips a safety stop (the accel/vel-limit "yellow light"),
        auto-recover via :meth:`recover_safe_stop` and retry once at reduced
        speed — the arm keeps its pose and the gripper its grip across the
        reset, so the retry resumes the same motion. Other failures (out of
        bounds, planning) are raised unchanged.
        """
        vel_scale = 1.0
        recovered = False
        for attempt in range(MOVE_RECOVER_RETRIES + 1):
            req, clamped = self._build_move_req(x, y, z, mode, vel_scale)
            try:
                result = await self._bridge.call_service(
                    "/dsr01/motion/move_line",
                    "dsr_msgs2/srv/MoveLine",
                    req,
                    timeout=30.0,
                )
                ok = bool(result.get("success", False)) if result else False
                msg = (
                    result.get("message", "Move command failed")
                    if result else "move_line service unavailable"
                )
            except RuntimeError as exc:
                ok, msg = False, str(exc)

            if ok:
                if mode != "relative" and clamped is not None:
                    tx, ty, tz = clamped
                    self._commanded_pos = {"x": tx, "y": ty, "z": tz}
                return {
                    "success": True,
                    "message": "Moved (recovered from safety stop)"
                    if recovered else "Moved",
                    "position": self._commanded_pos,
                    "recovered": recovered,
                }

            # Move failed. If it was a SW-recoverable safety stop, clear it
            # and retry once at reduced speed; otherwise surface the failure.
            if attempt < MOVE_RECOVER_RETRIES:
                rec = await self.recover_safe_stop()
                if rec["recovered"] and rec["from_state"] in _RECOVER_CONTROL:
                    logger.warning(
                        "move tripped %s; recovered to STANDBY, retrying at "
                        "%.0f%% speed", rec["from_state_name"],
                        MOVE_RECOVER_VEL_SCALE * 100,
                    )
                    vel_scale = MOVE_RECOVER_VEL_SCALE
                    recovered = True
                    continue
            raise RuntimeError(f"Move failed: {msg}")
        raise RuntimeError("Move failed")

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

    async def _skill_api_ready(self) -> bool:
        """True when skill_api_node answers GET /status with ready=true."""
        url = f"{self._skill_api_url}/status"
        loop = asyncio.get_running_loop()

        def _probe() -> bool:
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = json.loads(resp.read())
                return bool(data.get("ready"))
            except Exception:
                return False

        return await loop.run_in_executor(None, _probe)

    async def _ensure_skill_api(self) -> None:
        """Lazily start skill_api_node and wait until it is ready.

        Policy: started once on the first pick via the host bringup agent
        and left running (no stop-after).  skill_api_node hosts a MoveItPy
        runtime, so it needs the robot bringup (MoveIt) already up and its
        initialisation is slow — hence the generous readiness window.

        Raises:
            ConnectionError: no bringup agent to start it, or it did not
                become ready within ``SKILL_API_READY_TIMEOUT``.
        """
        if await self._skill_api_ready():
            return

        async with self._skill_api_lock:
            # Re-check: another concurrent pick may have started it.
            if await self._skill_api_ready():
                return

            if not self._launcher.agent_url:
                raise ConnectionError(
                    f"skill_api_node not running at {self._skill_api_url} and "
                    "no bringup agent (BRINGUP_AGENT_URL) configured to start "
                    "it; launch it manually: "
                    "'ros2 launch cup_stack skill_api.launch.py'"
                )

            logger.info(
                "skill_api not ready; starting via bringup agent (%s)",
                self._launcher.agent_url,
            )
            try:
                await self._launcher.start(SKILL_API_COMMAND)
            except Exception as exc:  # already starting / agent 409 / busy
                logger.info(
                    "skill_api start request returned %s; "
                    "polling for readiness anyway",
                    exc,
                )

            deadline = time.monotonic() + SKILL_API_READY_TIMEOUT
            while time.monotonic() < deadline:
                await asyncio.sleep(SKILL_API_POLL_INTERVAL)
                if await self._skill_api_ready():
                    logger.info("skill_api is ready")
                    return

            raise ConnectionError(
                f"skill_api_node did not become ready within "
                f"{SKILL_API_READY_TIMEOUT:.0f}s. Is the robot bringup "
                "(MoveIt) running? skill_api_node hosts MoveItPy and needs it."
            )

    async def pick_skill(
        self,
        x: float,
        y: float,
        cup_top_z: float | None = None,
        z: float | None = None,
        nested_count: int | None = None,
        ori: dict[str, float] | None = None,
        nested: int = 1,
    ) -> dict[str, Any]:
        """Proxy a single-cup pick to the ROS 2 skill_api_node.

        Coordinates are the **cup top centre** (base_link, m).  Z is
        resolved by the ROS 2 skill node with precedence
        ``z`` > ``cup_top_z`` > ``nested_count``:

        * ``cup_top_z`` — cup-top Z; skill node adds ``cup_grip_z_offset``.
        * ``z`` — raw gripper Z, no offset.
        * ``nested_count`` — number of nested cups in the source stack;
          skill node derives the gripper Z from
          ``pick_z_base + (nested_count - 1) * nest_inc``.
        * ``nested`` — same semantics as ``nested_count`` but with a
          default of 1; used when ``nested_count`` is not supplied. The
          effective nested count (``nested_count`` if given else
          ``nested``) is always sent as ``nested_count`` to the ROS node,
          which is harmless when ``z`` / ``cup_top_z`` win the precedence.

        Cup-stack geometry constants intentionally live in ROS 2
        (`cup_stack.skills.config.SkillStackConfig`).

        skill_api_node is started lazily on the first pick (via the host
        bringup agent) and left running for subsequent picks.

        Raises:
            ValueError: ``nested_count`` < 1 or ``nested`` < 1.
            ConnectionError: skill_api_node unreachable, or could not be
                started / did not become ready (see message).
            RuntimeError: skill node returned an HTTP error
                (message is ``"<status>: <body>"``).
        """
        if nested_count is not None and nested_count < 1:
            raise ValueError("'nested_count' must be >= 1")
        if nested < 1:
            raise ValueError("'nested' must be >= 1")

        effective_nested = nested_count if nested_count is not None else nested

        await self._ensure_skill_api()

        payload: dict[str, Any] = {"x": x, "y": y}
        if z is not None:
            payload["z"] = z
        if cup_top_z is not None:
            payload["cup_top_z"] = cup_top_z
        payload["nested_count"] = effective_nested
        if ori is not None:
            payload["ori"] = ori

        url = f"{self._skill_api_url}/skill/pick"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        def _call() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                raise RuntimeError(f"{exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise ConnectionError(
                    f"skill_api_node unreachable at {self._skill_api_url}: "
                    f"{exc.reason}"
                ) from exc

        logger.info("pick_skill -> %s %s", url, payload)
        return await self._run_skill_call(_call)

    # ── Pyramid config + skill ───────────────────────────────────────────────

    def _ensure_pyramid_center(self) -> dict[str, float]:
        """Return current pyramid center, lazy-initializing to configured HOME XY."""
        if self._pyramid_center is None:
            hx = self._robot_home["x"]
            hy = self._robot_home["y"]
            if not (self._move_limits.x_min <= hx <= self._move_limits.x_max
                    and self._move_limits.y_min <= hy <= self._move_limits.y_max):
                raise ValueError(
                    f"configured HOME ({hx:.3f},{hy:.3f}) outside workspace "
                    "XY limits"
                )
            self._pyramid_center = {"x": hx, "y": hy}
            self._recompute_slots()
        return self._pyramid_center

    def _recompute_slots(self) -> None:
        """Recompute the 6 absolute slot XYZ from (center, degree)."""
        if self._pyramid_center is None:
            self._pyramid_slots = {}
            return
        cx = self._pyramid_center["x"]
        cy = self._pyramid_center["y"]
        rad = math.radians(self._pyramid_degree)
        ux, uy = math.cos(rad), math.sin(rad)
        slots: dict[str, dict[str, float]] = {}
        for key, (lat, layer) in PYRAMID_SLOT_OFFSETS.items():
            slots[key] = {
                "x": cx + lat * ux,
                "y": cy + lat * uy,
                "z": PYRAMID_PLACE_Z_BASE + layer * PYRAMID_LAYER_HEIGHT,
            }
        self._pyramid_slots = slots

    def _validate_slot_z_bounds(self) -> None:
        """Reject when any slot Z exceeds workspace z_max."""
        if not self._pyramid_slots:
            return
        max_z = max(s["z"] for s in self._pyramid_slots.values())
        if max_z > self._move_limits.z_max:
            raise ValueError(
                f"top slot z={max_z:.3f} exceeds workspace z_max="
                f"{self._move_limits.z_max:.3f}"
            )

    def get_pyramid_config(self) -> dict[str, Any]:
        self._ensure_pyramid_center()
        return {
            "center": dict(self._pyramid_center),
            "degree": self._pyramid_degree,
            "pick_z": self._pyramid_pick_z,
            "slots": {k: dict(v) for k, v in self._pyramid_slots.items()},
        }

    def set_pyramid_config(
        self,
        center: dict[str, float] | None = None,
        degree: float | None = None,
        pick_z: float | None = None,
    ) -> dict[str, Any]:
        """Update pyramid config; recompute slots; validate z bounds."""
        # Initialize center from HOME if still unset and not provided.
        if center is None and self._pyramid_center is None:
            self._ensure_pyramid_center()

        if center is not None:
            cx, cy = float(center["x"]), float(center["y"])
            if not (self._move_limits.x_min <= cx <= self._move_limits.x_max
                    and self._move_limits.y_min <= cy <= self._move_limits.y_max):
                raise ValueError(
                    f"center ({cx:.3f},{cy:.3f}) outside workspace XY limits"
                )
            self._pyramid_center = {"x": cx, "y": cy}

        if degree is not None:
            self._pyramid_degree = float(degree) % 360.0

        if pick_z is not None:
            pz = float(pick_z)
            if not (self._move_limits.z_min <= pz <= self._move_limits.z_max):
                raise ValueError(
                    f"pick_z={pz:.3f} outside workspace Z limits "
                    f"[{self._move_limits.z_min:.3f},{self._move_limits.z_max:.3f}]"
                )
            self._pyramid_pick_z = pz

        self._recompute_slots()
        self._validate_slot_z_bounds()
        self._save_pyramid_config()
        return self.get_pyramid_config()

    def _load_pyramid_config(self) -> None:
        """Restore persisted pyramid center/degree/pick_z, if a state file exists.

        Best-effort: a missing or corrupt file leaves the in-memory defaults
        (center stays None → lazy-initialized from HOME on first read).
        """
        path = self._pyramid_state_path
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            center = data.get("center")
            if center is not None:
                self._pyramid_center = {
                    "x": float(center["x"]),
                    "y": float(center["y"]),
                }
            if data.get("degree") is not None:
                self._pyramid_degree = float(data["degree"]) % 360.0
            if data.get("pick_z") is not None:
                self._pyramid_pick_z = float(data["pick_z"])
            if self._pyramid_center is not None:
                self._recompute_slots()
            logger.info("Loaded pyramid config from %s", path)
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning(
                "Ignoring unreadable pyramid config at %s", path, exc_info=True
            )

    def _save_pyramid_config(self) -> None:
        """Persist pyramid center/degree/pick_z to the state file (best-effort)."""
        path = self._pyramid_state_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "center": dict(self._pyramid_center) if self._pyramid_center else None,
                "degree": self._pyramid_degree,
                "pick_z": self._pyramid_pick_z,
            }
            path.write_text(json.dumps(payload, indent=2))
        except OSError:
            logger.warning(
                "Failed to persist pyramid config to %s", path, exc_info=True
            )

    async def pyramid_skill(
        self,
        x: float,
        y: float,
        slot: str,
        nested: int = 1,
    ) -> dict[str, Any]:
        """Proxy a single pyramid pick-and-place to ROS 2 skill_api_node.

        Pulls (center, degree, pick_z) from the in-memory pyramid config
        and forwards both the pick (x,y,pick_z) and the absolute place
        (from the cached slot table) to /skill/pyramid_step.

        ``nested`` is the number of cups remaining in the *source* nest at
        ``(x, y)``: the top cup sits ``(nested - 1) * DEFAULT_NEST_INC``
        above the bottom one, so::

            pick_z = pyramid_pick_z + (nested - 1) * DEFAULT_NEST_INC

        Defaults to 1 → ``pick_z == pyramid_pick_z`` (unchanged behaviour),
        so existing callers that omit ``nested`` are unaffected.

        Raises:
            ValueError: invalid slot key, pick XY/Z outside workspace,
                ``nested`` < 1, or pyramid center unavailable.
            ConnectionError: skill_api_node unreachable / not ready.
            RuntimeError: skill_api_node returned an HTTP error.
        """
        if slot not in PYRAMID_SLOT_OFFSETS:
            raise ValueError(
                f"invalid slot '{slot}'; expected one of "
                f"{sorted(PYRAMID_SLOT_OFFSETS)}"
            )
        if nested < 1:
            raise ValueError("'nested' must be >= 1")
        if not (self._move_limits.x_min <= x <= self._move_limits.x_max
                and self._move_limits.y_min <= y <= self._move_limits.y_max):
            raise ValueError(
                f"pick ({x:.3f},{y:.3f}) outside workspace XY limits"
            )

        pick_z = self._pyramid_pick_z + (nested - 1) * DEFAULT_NEST_INC
        if not (self._move_limits.z_min <= pick_z <= self._move_limits.z_max):
            raise ValueError(
                f"pick_z={pick_z:.3f} (nested={nested}) outside workspace Z "
                f"limits [{self._move_limits.z_min:.3f},"
                f"{self._move_limits.z_max:.3f}]"
            )

        self._ensure_pyramid_center()
        place = self._pyramid_slots[slot]

        await self._ensure_skill_api()

        payload = {
            "x": float(x),
            "y": float(y),
            "pick_z": pick_z,
            "place_x": place["x"],
            "place_y": place["y"],
            "place_z": place["z"],
            "slot": slot,
        }

        logger.info("pyramid_skill (nested=%d) -> %s", nested, payload)
        return await self._post_pyramid_step(payload)

    async def unstack_skill(
        self,
        slot: str,
        x: float,
        y: float,
        nested: int = 1,
        home: bool = True,
    ) -> dict[str, Any]:
        """Pick the cup sitting in a pyramid ``slot`` and nest it at (x, y).

        The inverse of :meth:`pyramid_skill`: instead of spreading a cup
        from a source nest into a pyramid slot, this lifts the cup *out*
        of a slot (using the slot's cached absolute pose as the pick
        pose) and releases it into a destination nested column at
        ``(x, y)``.

        ``nested`` is the destination column height *after* this cup is
        added (1 = first/bottom cup).  The release Z grows with the
        column so each cup nests on top of the previous one::

            place_z = pyramid_pick_z + (nested - 1) * DEFAULT_NEST_INC

        Unstacking must proceed top-down (3m → 2r/2l → 1r/1m/1l); the
        caller is responsible for that ordering.

        Args:
            slot: slot to pick from (1l/1m/1r/2l/2r/3m).
            x, y: destination nest XY (base_link, m).
            nested: destination column height after placing (>= 1).

        Raises:
            ValueError: invalid slot, destination XY/Z outside workspace,
                or ``nested`` < 1.
            ConnectionError: skill_api_node unreachable / not ready.
            RuntimeError: skill_api_node returned an HTTP error.
        """
        if slot not in PYRAMID_SLOT_OFFSETS:
            raise ValueError(
                f"invalid slot '{slot}'; expected one of "
                f"{sorted(PYRAMID_SLOT_OFFSETS)}"
            )
        if nested < 1:
            raise ValueError("'nested' must be >= 1")
        if not (self._move_limits.x_min <= x <= self._move_limits.x_max
                and self._move_limits.y_min <= y <= self._move_limits.y_max):
            raise ValueError(
                f"destination ({x:.3f},{y:.3f}) outside workspace XY limits"
            )

        self._ensure_pyramid_center()
        pick = self._pyramid_slots[slot]

        place_z = self._pyramid_pick_z + (nested - 1) * DEFAULT_NEST_INC
        if not (self._move_limits.z_min <= place_z <= self._move_limits.z_max):
            raise ValueError(
                f"destination place_z={place_z:.3f} (nested={nested}) outside "
                f"workspace Z limits [{self._move_limits.z_min:.3f},"
                f"{self._move_limits.z_max:.3f}]"
            )

        await self._ensure_skill_api()

        payload = {
            "x": pick["x"],
            "y": pick["y"],
            "pick_z": pick["z"],
            "place_x": float(x),
            "place_y": float(y),
            "place_z": place_z,
            "slot": slot,
            # Hold the wrist at the HOME J6 yaw so it doesn't swing ~90° per
            # cup (avoids the alarm-1908 wrist-velocity spike on high picks).
            "grip_twist_deg": UNSTACK_GRIP_TWIST_DEG,
            # Skip the per-cup return to HOME except where the caller wants it
            # (the last cup of a sequence): far faster, no per-cup round-trip.
            "home": home,
        }
        logger.info(
            "unstack_skill (nested=%d, home=%s) -> %s", nested, home, payload
        )
        return await self._post_pyramid_step(payload)

    async def unstack_all_skill(
        self,
        x: float = DEFAULT_UNSTACK_DEST_X,
        y: float = DEFAULT_UNSTACK_DEST_Y,
        *,
        max_retry: int = 5,
        retry_delay: float = 3.0,
    ) -> dict[str, Any]:
        """Tear down the whole 3-2-1 pyramid into one nested column at (x, y).

        Server-side port of ``script/unstack.sh``: walks :data:`UNSTACK_SEQUENCE`
        (``3m → 2r → 2l → 1r → 1m → 1l``) and runs :meth:`unstack_skill` once per
        slot, raising the destination column height ``nested`` from 1 to 6 so the
        six cups nest on top of one another.

        Each step is retried up to ``max_retry`` times on a transient failure —
        a robot-motion service timeout (``409``, surfaced as ``RuntimeError``) or
        a skill_api / tunnel blip (``ConnectionError``) — sleeping ``retry_delay``
        seconds between attempts, exactly as ``unstack.sh``'s ``post_json`` does.
        A bad-input ``ValueError`` (slot/XYZ out of range) is **not** retried and
        propagates immediately so the caller fails fast before any cup moves.

        Unlike the single-cup skills this does not raise on a motion failure: if a
        step still fails after all retries the sequence stops and a structured
        result with ``success=False`` and the completed-step count is returned, so
        the UI can report "completed N/6" instead of an opaque 5xx.

        Args:
            x, y: destination nest XY (base_link, m). Defaults mirror unstack.sh.
            max_retry: attempts per step before giving up (>= 1).
            retry_delay: seconds slept between a failed attempt and the next.

        Returns:
            ``{success, skill, dest, total, completed, detail, steps}`` where
            ``steps`` is one entry per attempted slot
            (``{slot, nested, success, attempts, detail}``).

        Raises:
            ValueError: invalid destination XY/Z (no cup is moved).
        """
        steps: list[dict[str, Any]] = []
        total = len(UNSTACK_SEQUENCE)
        # Fresh run: clear any stop left set by a previous stop_all() so this
        # teardown is not aborted before it starts.
        self._stop_requested = False
        logger.info(
            "unstack_all_skill start: %d cups -> nest (x=%.3f, y=%.3f)",
            total, x, y,
        )

        for index, slot in enumerate(UNSTACK_SEQUENCE, start=1):
            # Honour a stop_all() between cups: the per-step skill_api /stop has
            # already interrupted+homed the in-flight cup, so just bail with a
            # partial result instead of issuing the next pick.
            if self._stop_requested:
                detail = (
                    f"정지 요청 — {index - 1}/{total} 컵 해체 후 중단"
                )
                logger.warning("unstack_all_skill stopped: %s", detail)
                return {
                    "success": False,
                    "skill": "unstack_all",
                    "dest": {"x": float(x), "y": float(y)},
                    "total": total,
                    "completed": index - 1,
                    "detail": detail,
                    "steps": steps,
                }
            nested = index  # destination column height: 1st cup=1 … 6th cup=6
            # Return to HOME only after the final cup — intermediate cups keep
            # the wrist at the grip yaw and stay low, so the whole teardown
            # homes once instead of six times (much faster, no per-cup swing).
            home = index == total
            last_err: Exception | None = None
            step_detail = ""
            attempts = 0

            for attempt in range(1, max_retry + 1):
                attempts = attempt
                try:
                    result = await self.unstack_skill(slot, x, y, nested, home=home)
                    step_detail = str(result.get("detail", ""))
                    last_err = None
                    break
                except ValueError:
                    # Bad input — never retryable; fail fast before touching a cup.
                    raise
                except (RuntimeError, ConnectionError) as exc:
                    last_err = exc
                    logger.warning(
                        "unstack_all step %d/%d slot=%s attempt %d/%d failed: %s",
                        index, total, slot, attempt, max_retry, exc,
                    )
                    if attempt < max_retry:
                        await asyncio.sleep(retry_delay)

            ok = last_err is None
            steps.append({
                "slot": slot,
                "nested": nested,
                "success": ok,
                "attempts": attempts,
                "detail": step_detail if ok else str(last_err),
            })

            if not ok:
                detail = (
                    f"slot={slot} (step {index}/{total}) 실패 — "
                    f"{last_err}; {index - 1}/{total} 컵 해체 후 중단"
                )
                logger.error("unstack_all_skill aborted: %s", detail)
                return {
                    "success": False,
                    "skill": "unstack_all",
                    "dest": {"x": float(x), "y": float(y)},
                    "total": total,
                    "completed": index - 1,
                    "detail": detail,
                    "steps": steps,
                }

        detail = (
            f"피라미드 해체 완료 ({total}/{total}) -> "
            f"nest (x={x:.3f}, y={y:.3f})"
        )
        logger.info("unstack_all_skill done: %s", detail)
        return {
            "success": True,
            "skill": "unstack_all",
            "dest": {"x": float(x), "y": float(y)},
            "total": total,
            "completed": total,
            "detail": detail,
            "steps": steps,
        }

    async def _post_pyramid_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a fully-resolved pick/place to skill_api_node /skill/pyramid_step.

        Shared by :meth:`pyramid_skill` (build) and :meth:`unstack_skill`
        (teardown): both reduce to one ``PlaceCupAtSkill`` pick→place→home.
        """
        url = f"{self._skill_api_url}/skill/pyramid_step"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        def _call() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                raise RuntimeError(f"{exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise ConnectionError(
                    f"skill_api_node unreachable at {self._skill_api_url}: "
                    f"{exc.reason}"
                ) from exc

        return await self._run_skill_call(_call)

    # ── Scan skill ───────────────────────────────────────────────────────────

    async def scan_skill(self) -> dict[str, Any]:
        """Proxy the scan skill to ROS 2 skill_api_node.

        Scan 은 인자가 없는 단일 스킬이라 본문이 비어 있다. ROS 2 측에서
        pos1 → pos2 → 초기 위치 순으로 PTP 이동하며 각 웨이포인트에서
        dwell 만큼 대기한다.

        Raises:
            ConnectionError: skill_api_node unreachable / not ready.
            RuntimeError: skill_api_node returned an HTTP error.
        """
        await self._ensure_skill_api()

        url = f"{self._skill_api_url}/skill/scan"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        def _call() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                raise RuntimeError(f"{exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise ConnectionError(
                    f"skill_api_node unreachable at {self._skill_api_url}: "
                    f"{exc.reason}"
                ) from exc

        logger.info("scan_skill -> %s", url)
        return await self._run_skill_call(_call)

    async def scan_square_skill(self) -> dict[str, Any]:
        """Proxy the 4-corner square scan skill to ROS 2 skill_api_node.

        2방향 scan 과 달리 카메라를 하향 고정한 채 base_link XY 평면의
        사각형 네 꼭짓점을 HOME EE 높이에서 순회한 뒤 시작 위치로 복귀한다.
        인자 없는 단일 스킬이라 본문이 비어 있다.

        Raises:
            ConnectionError: skill_api_node unreachable / not ready.
            RuntimeError: skill_api_node returned an HTTP error.
        """
        await self._ensure_skill_api()

        url = f"{self._skill_api_url}/skill/scan_square"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        def _call() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                raise RuntimeError(f"{exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise ConnectionError(
                    f"skill_api_node unreachable at {self._skill_api_url}: "
                    f"{exc.reason}"
                ) from exc

        logger.info("scan_square_skill -> %s", url)
        return await self._run_skill_call(_call)


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
