"""Calibration file I/O utility."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class CalibrationStore:
    """Load and save .npy calibration matrices from a config directory."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, filename: str) -> np.ndarray:
        path = self._dir / filename
        if not path.exists():
            return np.eye(4, dtype=float)
        matrix = np.load(str(path)).astype(float)
        logger.debug("Loaded calibration from %s", path)
        return matrix

    def save(self, filename: str, matrix: np.ndarray) -> None:
        path = self._dir / filename
        np.save(str(path), matrix)
        logger.info("Saved calibration to %s", path)

    def list_files(self) -> list[str]:
        return sorted(p.name for p in self._dir.glob("*.npy"))
