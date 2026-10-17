import math

import numpy as np
import pytest

from golf_etl.ball import track_ball
from tests.ball_clips import WHITE, address_pose, make_ball_clip

pytestmark = pytest.mark.media

IMPACT_S = 2.5  # frame 300 at 120fps


def test_yellow_ball_speed_and_direction(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", velocity=(-4.0, -20.0))
    flight = track_ball(clip, IMPACT_S, address_pose())
    assert flight.track == "ok"
    assert flight.ball_px_per_frame == pytest.approx(math.hypot(4, 20), rel=0.05)
    assert flight.start_dir == pytest.approx(-0.2, abs=0.05)
    assert abs(flight.impact_frame - 300) <= 2


def test_white_ball_speed(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", color=WHITE, velocity=(0.0, -16.0))
    flight = track_ball(clip, IMPACT_S, address_pose())
    assert flight.track == "ok"
    assert flight.ball_px_per_frame == pytest.approx(16, rel=0.05)


def test_shoe_turning_through_is_not_the_ball(tmp_path):
    # the left toe sits near (162, 610); the decoy is bigger than the ball
    clip = make_ball_clip(
        tmp_path / "clip.mp4", color=WHITE, velocity=(0.0, -16.0), decoy=(150, 615, 8)
    )
    flight = track_ball(clip, IMPACT_S, address_pose())
    assert flight.track == "ok"
    assert flight.ball_px_per_frame == pytest.approx(16, rel=0.05)


def test_spot_behind_the_heels_is_not_the_ball(tmp_path):
    # toes point right, so a ball can't sit left of the heels
    clip = make_ball_clip(tmp_path / "clip.mp4", rest=(40, 620), velocity=(0.0, -16.0))
    assert track_ball(clip, IMPACT_S, address_pose()).track == "lost: no ball launched"


def test_ball_left_of_the_golfer(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", rest=(180, 620), velocity=(4.0, -20.0))
    flight = track_ball(clip, IMPACT_S, address_pose(ankle_x=(0.7, 0.8), toes=-1))
    assert flight.track == "ok"
    assert flight.start_dir == pytest.approx(0.2, abs=0.05)


def test_face_on_ball_between_the_feet(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", rest=(270, 640), velocity=(0.0, -18.0))
    flight = track_ball(clip, IMPACT_S, address_pose(ankle_x=(0.35, 0.65), toes=0))
    assert flight.track == "ok"
    assert flight.ball_px_per_frame == pytest.approx(18, rel=0.05)


@pytest.mark.parametrize(
    "kwargs",
    [{"rest": None}, {"launch_frame": 10_000}],
    ids=["no ball", "ball never hit"],
)
def test_no_launch_is_lost(tmp_path, kwargs):
    clip = make_ball_clip(tmp_path / "clip.mp4", **kwargs)
    assert track_ball(clip, IMPACT_S, address_pose()).track == "lost: no ball launched"


def test_slow_frame_rate_is_lost(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", fps=30, frames=120, launch_frame=75)
    assert track_ball(clip, IMPACT_S, address_pose()).track == "lost: clip under 100fps"


def test_bumped_camera_is_lost(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4", shift_from=330)
    assert track_ball(clip, IMPACT_S, address_pose()).track == "lost: camera moved"


def test_no_golfer_at_address_is_lost(tmp_path):
    clip = make_ball_clip(tmp_path / "clip.mp4")
    no_pose = address_pose() * np.nan
    assert track_ball(clip, IMPACT_S, no_pose).track == "lost: no golfer at address"
    assert track_ball(clip, IMPACT_S, None).track == "lost: no golfer at address"
