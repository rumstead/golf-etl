import json

import pytest

from golf_etl.ball import BallFlight, grade, summary_line, write_ball_json


def ok(speed: float) -> BallFlight:
    return BallFlight("ok", impact_frame=300, ball_px_per_frame=speed, start_dir=-0.1, rise_px=250)


def test_grades_against_the_session_median():
    flights = [ok(10.0), ok(20.0), ok(18.5), ok(15.0), BallFlight("lost: camera moved")]
    graded, median = grade(flights, good=0.9, poor=0.78)
    assert median == pytest.approx(16.75)
    assert [f.grade for f in graded] == ["poor", "good", "good", "fair", None]


def test_nothing_tracked_has_no_median():
    graded, median = grade([BallFlight("lost: no ball launched")], good=0.9, poor=0.78)
    assert median is None
    assert graded[0].grade is None


def test_summary_line_counts_grades_and_lost():
    graded, median = grade([ok(10.0), ok(20.0), ok(21.0), BallFlight("lost: x")], 0.9, 0.78)
    assert summary_line(graded, median) == (
        "Ball flight: 2 good, 0 fair, 1 poor, 1 not tracked (median ball speed 20.0 px/frame)"
    )
    assert summary_line([BallFlight("lost: x")], None) == (
        "Ball flight: 0 good, 0 fair, 0 poor, 1 not tracked"
    )


def test_ball_json_has_metrics_points_and_params(tmp_path):
    flight = BallFlight(
        "ok",
        impact_frame=300,
        ball_px_per_frame=25.6,
        start_dir=-0.33,
        rise_px=293,
        points=((300, 330.6, 579.7), (301, 320.1, 547.2)),
        grade="good",
        ball="yellow",
    )
    write_ball_json(tmp_path / "ball.json", flight, median=21.3, good=0.9, poor=0.78)
    data = json.loads((tmp_path / "ball.json").read_text())
    assert data["track"] == "ok"
    assert data["grade"] == "good"
    assert data["ball_px_per_frame"] == 25.6
    assert data["points"] == [[300, 330.6, 579.7], [301, 320.1, 547.2]]
    assert data["params"]["long_edge"] == 960
    assert data["params"]["session_median"] == 21.3
    assert data["params"]["good"] == 0.9
