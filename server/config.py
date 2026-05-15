"""Server configuration loaded from environment or defaults."""

from dataclasses import dataclass, field
from pathlib import Path


def _default_workspace() -> Path:
    """Resolve the ROS 2 workspace root from the server package location."""
    return Path(__file__).resolve().parents[2] / "cup_stack" / "ros2"


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


@dataclass(frozen=True)
class CameraTopics:
    """Topic names for each camera source."""

    handineye_info: str = "/camera/camera/color/camera_info"
    handineye_color: str = "/camera/camera/color/image_raw/compressed"
    handineye_depth: str = "/camera/camera/aligned_depth_to_color/image_raw"
    handtoeye_info: str = "/camera/fixed_camera/color/camera_info"
    handtoeye_color: str = "/camera/fixed_camera/color/image_raw/compressed"
    handtoeye_depth: str = "/camera/fixed_camera/aligned_depth_to_color/image_raw"


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
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    cameras: CameraTopics = field(default_factory=CameraTopics)
    robot: RobotTopics = field(default_factory=RobotTopics)
    ports: ServicePorts = field(default_factory=ServicePorts)
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
