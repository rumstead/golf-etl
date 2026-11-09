import json

import pytest

from golf_etl.ball import (
    BallFlight,
    grade,
    grade_by_smash,
    grade_session,
    summary_line,
    write_ball_json,
)
from golf_etl.config import Settings


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


SMASHES_4859 = [1.08, 1.29, 1.27, 1.35, 1.15, 1.37, 1.41, 1.39, 1.33, 1.42, 1.36, 1.37]


def test_smash_grades_against_the_session_median():
    flights = [ok(10.0)] * len(SMASHES_4859)
    graded, median = grade_by_smash(flights, SMASHES_4859, good=0.97, poor=0.92)
    assert median == pytest.approx(1.355)
    grades = [f.grade for f in graded]
    assert grades[0] == "poor" and grades[4] == "poor"  # the owner's two poor strikes
    assert grades[1:3] == ["fair", "fair"]
    assert grades[5:] == ["good"] * 7


def test_a_swing_without_a_smash_read_has_no_grade_even_when_tracked():
    graded, _ = grade_by_smash([ok(10.0), ok(12.0), ok(11.0)], [1.3, None, 1.4], 0.97, 0.92)
    assert [f.grade for f in graded] == ["fair", None, "good"]


def test_a_lost_track_with_a_smash_read_is_graded():
    lost = BallFlight("lost: ball lost after launch")
    graded, _ = grade_by_smash([lost, ok(1.0)], [1.4, 1.3], 0.97, 0.92)
    assert graded[0].grade == "good"


def test_fewer_than_three_smash_reads_keeps_px_grading():
    flights = [ok(10.0), ok(20.0), ok(18.5)]
    graded, median, basis = grade_session(flights, [1.3, None, 1.4], Settings())
    assert basis == "ball_px" and median == pytest.approx(18.5)
    assert [f.grade for f in graded] == ["poor", "good", "good"]


def test_three_smash_reads_switch_the_session_to_smash():
    graded, median, basis = grade_session([ok(10.0)] * 3, [1.3, 1.4, 1.2], Settings())
    assert basis == "sim_smash" and median == pytest.approx(1.3)


def test_summary_line_names_the_smash_basis():
    graded, median = grade_by_smash([ok(1.0)] * 4, [1.3, 1.4, None, 1.1], 0.97, 0.92)
    assert summary_line(graded, median, "sim_smash") == (
        "Ball flight: 2 good, 0 fair, 1 poor, 1 ungraded (graded by sim smash factor, median 1.30)"
    )


def test_ball_json_names_its_basis(tmp_path):
    write_ball_json(
        tmp_path / "ball.json", ok(1.0), median=1.3, good=0.97, poor=0.92, basis="sim_smash"
    )
    assert json.loads((tmp_path / "ball.json").read_text())["params"]["basis"] == "sim_smash"
