import json

import cv2
import numpy as np
import pytest

from golf_etl.ball import RestSpot
from golf_etl.strike import (
    contact_frame,
    diameters,
    hosel_x,
    read_strike,
    session_radius,
    write_strike_json,
)

H, W = 400, 600
BALL = (400.0, 300.0, 10.0)  # x, y, radius in px
REST = RestSpot(BALL[0] / W, BALL[1] / H, BALL[2] / H, "white", 0.0, 0.0, face_on=False)
MAT = (70, 110, 50)


def frame(ball: tuple[float, float] | None = BALL[:2], hosel: float | None = None) -> np.ndarray:
    """A green mat with a white ball, and a light shaft coming down from the upper left that
    meets the ball's row at `hosel`."""
    img = np.full((H, W, 3), MAT, np.uint8)
    if ball is not None:
        cv2.circle(img, (round(ball[0]), round(ball[1])), round(BALL[2]), (245, 245, 245), -1)
    if hosel is not None:
        top = (round(hosel - 60), round(BALL[1] - 110))
        cv2.line(img, top, (round(hosel), round(BALL[1])), (200, 200, 200), 3)
    return img


def covered() -> np.ndarray:
    """The club's blur over the middle of the ball."""
    img = frame()
    cv2.rectangle(img, (390, 290), (410, 310), (150, 150, 150), -1)
    return img


def test_contact_is_the_later_of_impact_and_the_frame_after_with_the_ball_still():
    reference = frame()
    frames = [(0.0, frame()), (0.01, frame(hosel=380)), (0.02, frame(ball=(430, 280)))]
    assert contact_frame(frames, 1, reference, REST) == 1
    frames = [(0.0, frame()), (0.01, frame()), (0.02, frame(hosel=380))]
    assert contact_frame(frames, 1, reference, REST) == 2


def test_contact_is_never_before_impact():
    # the club covers the ball at impact and the ball is gone after: the still frame before
    # impact has the club short of the ball
    frames = [(0.0, frame(hosel=300)), (0.01, covered()), (0.02, frame(ball=None))]
    assert contact_frame(frames, 1, frame(), REST) is None


def test_no_contact_when_the_ball_is_gone_in_every_frame():
    frames = [(0.0, frame(ball=None))]
    assert contact_frame(frames, 0, frame(), REST) is None


def test_hosel_is_where_the_shaft_meets_the_ball_row():
    assert hosel_x(frame(hosel=370), REST, side=1) == pytest.approx(370, abs=2)


def test_no_hosel_without_a_shaft():
    assert hosel_x(frame(), REST, side=1) is None


def test_strike_is_in_ball_diameters_toward_the_toe():
    frames = [(0.0, frame(hosel=380)), (0.01, frame(hosel=370)), (0.02, frame(ball=None))]
    strike = read_strike(frames, 0, frame(), REST, side=1)
    assert strike.contact == 1
    # a 3px line: the fit lands on one of its edges
    assert diameters(strike, 10.0) == pytest.approx((400 - 370) / 20, abs=0.15)


def test_the_session_radius_sets_the_scale():
    strikes = [read_strike([(0.0, frame(hosel=370))], 0, frame(), REST, side=1)] * 3
    assert session_radius(strikes + [None]) == 10.0
    assert diameters(strikes[0], 15.0) == pytest.approx(diameters(strikes[0], 10.0) * 2 / 3)
    assert session_radius([None]) is None


def test_strike_is_mirrored_when_the_golfer_stands_right_of_the_ball():
    mirror = lambda img: np.ascontiguousarray(img[:, ::-1])  # noqa: E731
    rest = RestSpot((W - BALL[0]) / W, REST.y, REST.radius, "white", 0.0, 0.0, face_on=False)
    frames = [(0.0, mirror(frame(hosel=370)))]
    strike = read_strike(frames, 0, mirror(frame()), rest, side=-1)
    assert diameters(strike, 10.0) == pytest.approx(1.5, abs=0.15)


def test_no_strike_when_the_contact_frame_shows_no_shaft():
    # the frame before has the club short of the ball, so it does not stand in
    frames = [(0.0, frame(hosel=300)), (0.01, frame())]
    strike = read_strike(frames, 0, frame(), REST, side=1)
    assert strike.contact == 1 and diameters(strike, 10.0) is None


def test_strike_json_keeps_the_points_it_was_measured_from(tmp_path):
    strike = read_strike([(0.5, frame(hosel=370))], 0, frame(), REST, side=1)
    write_strike_json(tmp_path / "strike.json", strike, 12.0)
    data = json.loads((tmp_path / "strike.json").read_text())
    assert data["contact_s"] == 0.5 and data["ball"] == [400.0, 300.0, 10.0]
    assert data["radius_px"] == 12.0 and data["strike"] == pytest.approx(30 / 24, abs=0.15)
    assert data["hosel"][0] == pytest.approx(370, abs=2)
    write_strike_json(tmp_path / "none.json", None, 12.0)
    assert json.loads((tmp_path / "none.json").read_text())["strike"] is None
