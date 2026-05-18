"""Server configuration loaded from environment or defaults."""

from dataclasses import dataclass, field
from pathlib import Path


def _default_workspace() -> Path:
    """Resolve the ROS 2 workspace root from the server package location."""
    return Path(__file__).resolve().parents[2] / "cup_stack" / "ros2"


def _server_dir() -> Path:
    """Server repo dir holding the canonical bringup scripts."""
    return Path(__file__).resolve().parents[1]


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

    - ``exo``  = eye-to-hand  (fixed/external camera, serial 24232207744)
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
class WorkspaceLimits:
    """Robot workspace safe zone limits (meters)."""

    x_min: float = -0.5
    x_max: float = 0.5
    y_min: float = -0.5
    y_max: float = 0.5
    z_min: float = 0.25
    z_max: float = 0.55
    grid_spacing: float = 0.05


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
    ports: ServicePorts = field(default_factory=ServicePorts)
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
