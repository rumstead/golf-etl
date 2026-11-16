import json

import cv2
import numpy as np
import pytest

from golf_etl.ball import RestSpot
from golf_etl.strike import contact_frame, hosel_x, read_strike, write_strike_json

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


def test_contact_is_the_last_frame_with_the_ball_still():
    reference = frame()
    frames = [(0.0, frame()), (0.01, frame(hosel=380)), (0.02, frame(ball=(430, 280)))]
    assert contact_frame(frames, reference, REST) == 1


def test_no_contact_when_the_ball_is_gone_in_every_frame():
    frames = [(0.0, frame(ball=None))]
    assert contact_frame(frames, frame(), REST) is None


def test_hosel_is_where_the_shaft_meets_the_ball_row():
    assert hosel_x(frame(hosel=370), REST, side=1) == pytest.approx(370, abs=2)


def test_no_hosel_without_a_shaft():
    assert hosel_x(frame(), REST, side=1) is None


def test_strike_is_in_ball_diameters_toward_the_toe():
    reference = frame()
    frames = [(0.0, frame(hosel=380)), (0.01, frame(hosel=370)), (0.02, frame(ball=None))]
    strike = read_strike(frames, reference, REST, side=1)
    assert strike.contact == 1
    assert strike.diameters == pytest.approx((400 - 370) / 20, abs=0.15)  # a 3px line


def test_strike_is_mirrored_when_the_golfer_stands_right_of_the_ball():
    mirror = lambda img: np.ascontiguousarray(img[:, ::-1])  # noqa: E731
    rest = RestSpot((W - BALL[0]) / W, REST.y, REST.radius, "white", 0.0, 0.0, face_on=False)
    frames = [(0.0, mirror(frame(hosel=370)))]
    strike = read_strike(frames, mirror(frame()), rest, side=-1)
    assert strike.diameters == pytest.approx(1.5, abs=0.15)


def test_no_strike_when_the_contact_frame_shows_no_shaft():
    # the frame before has the club short of the ball, so it does not stand in
    frames = [(0.0, frame(hosel=300)), (0.01, frame())]
    strike = read_strike(frames, frame(), REST, side=1)
    assert strike.contact == 1 and strike.diameters is None


def test_strike_json_keeps_the_points_it_was_measured_from(tmp_path):
    frames = [(0.5, frame(hosel=370))]
    write_strike_json(tmp_path / "strike.json", read_strike(frames, frame(), REST, side=1))
    data = json.loads((tmp_path / "strike.json").read_text())
    assert data["contact_s"] == 0.5 and data["ball"] == [400.0, 300.0, 10.0]
    assert data["hosel"][0] == pytest.approx(370, abs=2)
    write_strike_json(tmp_path / "none.json", None)
    assert json.loads((tmp_path / "none.json").read_text())["strike"] is None
