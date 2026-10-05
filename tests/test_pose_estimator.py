import numpy as np
import pytest

from golf_etl.config import Settings
from golf_etl.pose import PoseEstimator

pytestmark = pytest.mark.media


def test_no_person_gives_nan_landmarks():
    frames = [(i / 30, np.full((360, 640, 3), 40, np.uint8)) for i in range(5)]
    track = PoseEstimator(Settings().pose_model_path).track(frames)
    assert track.landmarks.shape == (5, 33, 3)
    assert np.all(np.isnan(track.landmarks))
    assert track.aspect == pytest.approx(640 / 360)
