"""Server configuration loaded from environment or defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_workspace() -> Path:
    """Resolve the ROS 2 workspace root from the server package location."""
    return Path(__file__).resolve().parents[2] / "cup_stack" / "ros2"


def _server_dir() -> Path:
    """Server repo dir holding the canonical bringup scripts."""
    return Path(__file__).resolve().parents[1]


def _default_state_dir() -> Path:
    """Writable dir for runtime state that must survive container restarts.

    Honors ``CUP_STATE_DIR`` (set to ``/app/data`` in docker-compose, where the
    ``robot_state`` named volume is mounted). The ``__file__``-relative fallback
    is only for local dev — inside the image the package is pip-installed into
    site-packages, so that path would NOT line up with the volume mount.
    """
    env = os.getenv("CUP_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = field(
        default_factory=lambda: ["*"],
    )
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


@dataclass(frozen=True)
class RosBridgeConfig:
    host: str = "localhost"
    port: int = 9090


@dataclass(frozen=True)
class SkillApiConfig:
    """Location of the ROS 2 skill_api_node HTTP server (PickCupSkill).

    Overridable via the ``SKILL_API_URL`` env var on the robot service.
    """

    host: str = "localhost"
    port: int = 8765

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path = field(default_factory=_default_workspace)
    launch_package: str = "cup_stack"
    bringup_sim_script: str = "bringup_sim.sh"
    bringup_real_script: str = "bringup_real.sh"

    @property
    def src_dir(self) -> Path:
        return self.root / "src"

    @property
    def cup_stack_dir(self) -> Path:
        return self.src_dir / "cup_stack"

    @property
    def config_dir(self) -> Path:
        return self.cup_stack_dir / "config"

    @property
    def server_dir(self) -> Path:
        """Dir holding the canonical bringup scripts (server repo root)."""
        return _server_dir()


@dataclass(frozen=True)
class CameraTopics:
    """Topic names for each camera source.

    Two RealSense cameras are launched under distinct namespaces by serial:

    - ``exo``  = eye-to-hand  (fixed/external camera, serial 242322077444)
                 → topics under ``/exo/exo/``
    - ``hand`` = eye-in-hand  (EE-mounted camera, serial 140122076335)
                 → topics under ``/hand/hand/``

    ``*_color`` points at the ``/compressed`` image_transport sub-topic
    because the WebSocket stream subscribes as ``CompressedImage`` and
    passes JPEG frames straight through.
    """

    hand_info: str = "/hand/hand/color/camera_info"
    hand_color: str = "/hand/hand/color/image_raw/compressed"
    hand_depth: str = "/hand/hand/aligned_depth_to_color/image_raw"
    exo_info: str = "/exo/exo/color/camera_info"
    exo_color: str = "/exo/exo/color/image_raw/compressed"
    exo_depth: str = "/exo/exo/aligned_depth_to_color/image_raw"


@dataclass(frozen=True)
class RobotTopics:
    joint_states: str = "/dsr01/joint_states"


@dataclass(frozen=True)
class RobotHome:
    """Configured HOME end-effector XY in base_link frame (meters)."""

    x: float = 0.45
    y: float = 0.0


@dataclass(frozen=True)
class WorkspaceLimits:
    """Robot workspace safe zone limits (meters)."""

    x_min: float = -0.5
    x_max: float = 0.54
    y_min: float = -0.5
    y_max: float = 0.5
    z_min: float = 0.25
    z_max: float = 0.55
    grid_spacing: float = 0.05


@dataclass(frozen=True)
class FallenCupConfig:
    """Fallen-cup detection (YOLO) launch defaults.

    ``weights_path`` is the absolute path to the trained YOLOv-seg ``best.pt``.
    Empty string falls back to the launch file default
    (speed_stack_yolo_seg share ``weights/best.pt``). Override via the
    ``FALLEN_CUP_WEIGHTS`` env var on the robot service.
    """

    weights_path: str = ""
    conf: float = 0.70
    imgsz: int = 1280
    use_depth: bool = True
    # GPU 추론 기본값 ("0" = CUDA:0). CPU 추론은 코어를 통째로 잡아먹어
    # (~585% CPU) ros2_control_node 의 100Hz 스트리밍 루프를 굶겨 모든 JTC
    # 스킬(pyramid/fallen)에서 stutter 를 유발한다. CUDA 불가 시 노드가 cpu 로
    # 자동 fallback. FALLEN_CUP_DEVICE env 로 override 가능.
    # "cuda" 사용 ("0" 은 launch 에서 INTEGER 로 파싱돼 STRING 파라미터와
    # 타입 불일치로 노드가 죽음).
    device: str = "cuda"


@dataclass(frozen=True)
class FallenCupTopics:
    """Topics published by the fallen-cup detection node."""

    pose2d: str = "/fallen_cup/pose2d"
    grasp_pose: str = "/fallen_cup/grasp_pose"
    cups_pose2d: str = "/fallen_cup/cups_pose2d"
    cups_grasp_poses: str = "/fallen_cup/cups_grasp_poses"


@dataclass(frozen=True)
class ServicePorts:
    robot: int = 8001
    handineye: int = 8002
    handtoeye: int = 8003


@dataclass
class AppSettings:
    server: ServerConfig = field(default_factory=ServerConfig)
    rosbridge: RosBridgeConfig = field(default_factory=RosBridgeConfig)
    skill_api: SkillApiConfig = field(default_factory=SkillApiConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    cameras: CameraTopics = field(default_factory=CameraTopics)
    robot: RobotTopics = field(default_factory=RobotTopics)
    robot_home: RobotHome = field(default_factory=RobotHome)
    ports: ServicePorts = field(default_factory=ServicePorts)
    state_dir: Path = field(default_factory=_default_state_dir)
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    fallen_cup: FallenCupConfig = field(default_factory=FallenCupConfig)
    fallen_cup_topics: FallenCupTopics = field(default_factory=FallenCupTopics)
