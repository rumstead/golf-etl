"""Panel crops and their hand-read labels from tests/data/sim."""

from pathlib import Path

import cv2
import numpy as np
import yaml

DATA = Path(__file__).parent / "data" / "sim"


def load(name: str) -> tuple[np.ndarray, dict[str, str | None]]:
    """Return the RGB crop and its labels (None when unlabeled or blank)."""
    bgr = cv2.imread(str(DATA / f"{name}.png"))
    labels = yaml.safe_load((DATA / "labels.yaml").read_text())[f"{name}.png"]
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), labels
