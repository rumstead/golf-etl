import numpy as np
import pytest

from tests.poses import still_track, swing_track


def test_hand_speed_peaks_near_impact():
    track = swing_track(10.0)
    speed = track.hand_speed()
    assert track.times[int(np.nanargmax(speed))] == pytest.approx(10.0, abs=0.05)


def test_still_person_has_no_hand_speed():
    assert np.nanmax(still_track(5.0).hand_speed()) == pytest.approx(0.0)


def test_hand_height_highest_at_top():
    track = swing_track(10.0)
    before = track.times < 10.0
    top = track.times[before][int(np.argmax(track.hand_height()[before]))]
    assert top == pytest.approx(9.6, abs=0.05)


def test_nearest_returns_closest_sample():
    track = swing_track(10.0)
    lm = track.nearest(10.001)
    assert lm.shape == (33, 3)


def test_missing_pose_is_nan_speed():
    track = swing_track(10.0, missing=(8.0, 8.5))
    speed = track.hand_speed()
    gap = (track.times > 8.05) & (track.times < 8.45)
    assert np.all(np.isnan(speed[gap]))
