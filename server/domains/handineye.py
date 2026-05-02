"""Hand-in-eye domain: EE-mounted camera calibration and coordinate transforms."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..ros.bridge import RosBridge
from ..services.calibration import CalibrationStore

logger = logging.getLogger(__name__)

DEFAULT_HANDINEYE_FILE = "T_gripper2camera.npy"


class HandInEyeDomain:
    """Manages hand-in-eye calibration and pixel-to-base conversion."""

    def __init__(
        self,
        bridge: RosBridge,
        calibration_store: CalibrationStore,
        camera_info_topic: str,
        color_topic: str,
    ) -> None:
        self._bridge = bridge
        self._store = calibration_store
        self._camera_info_topic = camera_info_topic
        self._color_topic = color_topic
        self._intrinsics: dict[str, float] | None = None

    def load_calibration(self) -> np.ndarray:
        return self._store.load(DEFAULT_HANDINEYE_FILE)

    def save_calibration(self, matrix: np.ndarray) -> None:
        self._store.save(DEFAULT_HANDINEYE_FILE, matrix)

    def get_calibration(self) -> dict[str, Any]:
        matrix = self.load_calibration()
        return {
            "file": DEFAULT_HANDINEYE_FILE,
            "matrix": matrix.tolist(),
            "shape": list(matrix.shape),
        }

    def update_calibration(self, matrix_data: list[list[float]]) -> dict[str, Any]:
        matrix = np.array(matrix_data, dtype=float)
        self.save_calibration(matrix)
        return self.get_calibration()

    def pixel_to_base(
        self,
        pixel_x: int,
        pixel_y: int,
        depth_mm: float,
        ee_matrix: np.ndarray,
        gripper_to_camera: np.ndarray,
    ) -> tuple[float, float, float]:
        if self._intrinsics is None:
            raise ValueError("Camera intrinsics not received yet")

        fx = self._intrinsics["fx"]
        fy = self._intrinsics["fy"]
        ppx = self._intrinsics["ppx"]
        ppy = self._intrinsics["ppy"]

        z_m = depth_mm / 1000.0
        cam_x = (pixel_x - ppx) * z_m / fx
        cam_y = (pixel_y - ppy) * z_m / fy
        cam_point = np.array([cam_x, cam_y, z_m, 1.0], dtype=float)

        base_to_camera = ee_matrix @ gripper_to_camera
        base_point = base_to_camera @ cam_point
        return (
            float(base_point[0]),
            float(base_point[1]),
            float(base_point[2]),
        )

    def on_camera_info(self, msg: dict[str, Any]) -> None:
        k = msg.get("k", [0.0] * 9)
        self._intrinsics = {
            "fx": k[0],
            "fy": k[4],
            "ppx": k[2],
            "ppy": k[5],
        }
