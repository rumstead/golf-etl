import numpy as np
import pytest

from golf_etl.frames import pick_positions
from golf_etl.measures import NOSE, body_measures, by_grade
from golf_etl.pose import (
    LEFT_FOOT_INDEX,
    LEFT_HEEL,
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_FOOT_INDEX,
    RIGHT_HEEL,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from tests.poses import swing_track

LEG = 0.35  # the synthetic golfer's hip to ankle height, in frame heights


def _swing(toes: float = 0.05):
    """A swing seen down the line with the toes pointing `toes` frame widths from the heels,
    and its positions. The head is at (0.5, 0.2) throughout."""
    track = swing_track(10.0)
    track.landmarks[:, NOSE, :2] = (0.5, 0.2)
    track.landmarks[:, [LEFT_HEEL, RIGHT_HEEL], :2] = (0.5, 0.92)
    track.landmarks[:, [LEFT_FOOT_INDEX, RIGHT_FOOT_INDEX], :2] = (0.5 + toes, 0.92)
    return track, pick_positions(track, 10.0, still_speed=0.15)


def _from(track, t: float) -> np.ndarray:
    return track.times >= t


def test_a_still_body_measures_zero():
    track, p = _swing()
    body = body_measures(track, p)
    assert body["view"] == "down the line"
    assert body["head_top_side"] == body["head_impact_up"] == body["hips_impact_side"] == 0
    assert body["spine_address"] == pytest.approx(0)  # shoulders straight over the hips
    assert body["hands_top"] == pytest.approx((0.3 - 0.2) / LEG, abs=0.02)


def test_address_is_the_still_stretch_not_its_last_moment():
    track, p = _swing()
    assert p.still_from_s is not None and p.address_s - p.still_from_s > 0.5
    # the shoulders start turning toward the ball in the last 0.1s of the still stretch
    turning = (track.times > p.address_s - 0.1) & (track.times <= p.address_s)
    track.landmarks[np.ix_(turning, [LEFT_SHOULDER, RIGHT_SHOULDER], [0])] += 0.05
    assert body_measures(track, p)["spine_address"] == pytest.approx(0)


def test_moves_are_in_leg_lengths_toward_the_ball_and_up():
    track, p = _swing()
    late = _from(track, 9.9)
    track.landmarks[late, NOSE, 0] += 0.035 * 9 / 16  # 0.1 leg lengths toward the toes
    track.landmarks[late, NOSE, 1] += 0.07  # and 0.2 down
    track.landmarks[np.ix_(late, [LEFT_HIP, RIGHT_HIP], [0])] += 0.07 * 9 / 16
    body = body_measures(track, p)
    assert body["head_impact_side"] == pytest.approx(0.1)
    assert body["head_impact_up"] == pytest.approx(-0.2)
    assert body["hips_impact_side"] == pytest.approx(0.2)
    assert body["head_top_side"] == 0
    assert body["spine_impact"] < 0  # the hips moved in under the shoulders


def test_side_follows_the_toes():
    track, p = _swing(toes=-0.05)
    track.landmarks[_from(track, 9.9), NOSE, 0] += 0.035 * 9 / 16
    assert body_measures(track, p)["head_impact_side"] == pytest.approx(-0.1)


def test_face_on_side_is_to_the_right_of_the_picture():
    track, p = _swing(toes=0.0)
    track.landmarks[_from(track, 9.9), NOSE, 0] += 0.035 * 9 / 16
    body = body_measures(track, p)
    assert body["view"] == "face-on"
    assert body["head_impact_side"] == pytest.approx(0.1)


def test_a_joint_the_pose_is_unsure_of_is_not_measured():
    track, p = _swing()
    track.landmarks[_from(track, 9.9), NOSE, 2] = 0.2
    track.landmarks[np.ix_(_from(track, 9.9), [LEFT_WRIST], [2])] = 0.2
    body = body_measures(track, p)
    assert body["head_impact_side"] is None and body["head_top_side"] == 0
    assert body["hands_impact_up"] is not None  # one hand seen is enough


def test_no_hands_at_the_top_leaves_hands_top_blank():
    track, p = _swing()
    near_top = (track.times > p.top_s - 0.1) & (track.times < p.top_s + 0.2)
    track.landmarks[np.ix_(near_top, [LEFT_WRIST, RIGHT_WRIST], [2])] = 0.1
    p = pick_positions(track, 10.0, still_speed=0.15)
    assert body_measures(track, p)["hands_top"] is None


def test_no_pose_measures_nothing():
    track, p = _swing()
    track.landmarks[:] = np.nan
    body = body_measures(track, p)
    assert body["view"] is None and body["head_top_side"] is None


def test_by_grade_averages_the_values_each_grade_has():
    grades = ["good", "poor", "good", None, "poor"]
    counts, means = by_grade(grades, {"smash": [1.4, 1.1, 1.3, 1.2, None], "tempo": [None] * 5})
    assert counts == {"good": 2, "poor": 2}
    assert means["smash"] == {"good": pytest.approx(1.35), "poor": pytest.approx(1.1)}
    assert means["tempo"] == {"good": None, "poor": None}
