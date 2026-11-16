from datetime import UTC, datetime

import pytest

from golf_etl.ball import BallFlight
from golf_etl.detect import Rejected, Swing
from golf_etl.frames import Timing
from golf_etl.measures import BODY_COLUMNS
from golf_etl.session import (
    SessionSummary,
    session_id,
    session_summary,
    video_time,
    write_index,
    write_session_md,
)
from golf_etl.sim import FIELDS, SimShot
from tests.poses import swing_track

WHEN = datetime(2026, 10, 1, 15, 4, tzinfo=UTC)


def test_session_id_is_capture_minute_plus_short_checksum():
    assert session_id(WHEN, "ab12cd34ef567890") == "2026-10-01-1504-ab12cd34"


def summary(**kw):
    base = dict(
        session_id="2026-10-01-1504-ab12cd34",
        source_name="IMG_1234.MOV",
        captured=WHEN,
        checksum="ab12cd34ef567890",
        version="test",
        swings=[],
        rejected=[],
    )
    return SessionSummary(**(base | kw))


def test_session_md_lists_swings_rejections_and_duplicates(tmp_path):
    path = tmp_path / "session.md"
    write_session_md(
        path,
        summary(
            swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))],
            rejected=[Rejected(30.1, 0.4, "hand speed peak +900ms from the sound")],
            duplicates=["IMG_1234 (1).MOV"],
        ),
    )
    text = path.read_text()
    assert "- Swings: 1" in text
    assert "| swing-01 | 12.40s | 1.00 | +16ms |" in text
    assert "| 30.10s | 0.40 | hand speed peak +900ms from the sound |" in text
    assert "- IMG_1234 (1).MOV" in text
    assert "## How to read this session" in text
    assert "`06-sequence.jpg`" in text
    assert "`07-after-shot.jpg`" in text


def test_session_md_grades_ball_flight_per_swing(tmp_path):
    path = tmp_path / "session.md"
    swings = [Swing(65.81, 0.45, 50.0, swing_track(65.81)), Swing(914.32, 0.38, -83.0, None)]
    balls = [
        BallFlight("ok", ball_px_per_frame=14.4, start_dir=-0.36, grade="poor"),
        BallFlight("lost: no ball launched"),
    ]
    s = summary(swings=swings, balls=balls, ball_median=21.3, strikes=[1.234, None])
    write_session_md(path, s)
    text = path.read_text()
    assert (
        "Ball flight: 0 good, 0 fair, 1 poor, 1 not tracked (median ball speed 21.3 px/frame)"
        in text
    )
    header = "| Swing | Impact | Strength | Hand speed peak | Ball px/frame | Start dir | Strike |"
    assert f"{header} Grade | Track |" in text
    assert "| swing-01 | 1:05.8 | 0.45 | +50ms | 14.4 | -0.36 | 1.23 | poor | ok |" in text
    assert "| swing-02 | 15:14.3 | 0.38 | -83ms | | | | | lost: no ball launched |" in text
    guide = text.split("| Swing |")[0]  # the guide explains the columns
    assert "Ball px/frame" in guide and "Strike is where the ball sat on the face" in guide


def test_session_md_without_strikes_leaves_the_column_blank(tmp_path):
    path = tmp_path / "session.md"
    swings = [Swing(65.81, 0.45, 50.0, swing_track(65.81))]
    balls = [BallFlight("ok", ball_px_per_frame=14.4, start_dir=-0.36, grade="poor")]
    write_session_md(path, summary(swings=swings, balls=balls, ball_median=21.3))
    assert "| swing-01 | 1:05.8 | 0.45 | +50ms | 14.4 | -0.36 | | poor | ok |" in path.read_text()


def test_session_md_has_a_timing_table(tmp_path):
    path = tmp_path / "session.md"
    timing = Timing(
        takeaway_start_s=11.2,
        top_s=12.0,
        impact_s=12.4,
        takeaway_start_frame=144,
        top_frame=240,
        impact_frame=288,
        start_from_club=True,
    )
    write_session_md(
        path, summary(swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))], timings=[timing])
    )
    text = path.read_text()
    assert "## Timing" in text
    assert "| swing-01 | 144 (11.20s) | 240 (12.00s) | 288 (12.40s) | 0.80s | 0.40s | 2.0 |" in text


def test_timing_row_leaves_durations_out_when_the_top_is_out_of_frame(tmp_path):
    path = tmp_path / "session.md"
    timing = Timing(
        takeaway_start_s=11.2,
        top_s=12.0,
        impact_s=12.4,
        takeaway_start_frame=144,
        top_frame=240,
        impact_frame=288,
        top_in_frame=False,
        start_from_club=True,
    )
    write_session_md(
        path, summary(swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))], timings=[timing])
    )
    row = "| swing-01 | 144 (11.20s) | hands not seen at the top | 288 (12.40s) | | | |"
    assert row in path.read_text()


def test_sim_table_lists_every_swing_with_blanks_for_unread_fields(tmp_path):
    path = tmp_path / "session.md"
    read = {
        "club_speed": "73.2",
        "ball_speed": "89.7",
        "smash": "1.23",
        "carry_raw": "110.0",
        "carry_game": "109.8",
        "total_length": "121.4",
        "peak_height": "33.8",
        "back_spin": "3365",
        "total_spin": "3554",
        "club_path": "1.0 I-O",
        "face_to_path": "5.5 C",
        "face_to_target": "4.6 O",
    }
    partial = read | {"carry_game": None, "face_to_target": None}
    sims = [
        SimShot(read, dict.fromkeys(FIELDS, 4), 20, {}),
        SimShot(partial, {f: 4 if partial[f] else 0 for f in FIELDS}, 20, {}),
        None,
    ]
    swings = [Swing(t, 1.0, 16.0, swing_track(t)) for t in (24.4, 49.6, 86.6)]
    timing = Timing(23.2, 24.0, 24.4, 144, 240, 288)
    write_session_md(path, summary(swings=swings, sims=sims, timings=[timing] * 3))
    text = path.read_text()
    sim = text.split("## Sim")[1].split("## Timing")[0]
    assert "`07-after-shot.jpg`" in sim and "next swing" in sim and "did not change" in sim
    header = (
        "| Swing | Club mph | Ball mph | Smash | Carry raw yd | Carry game yd | Total yd "
        "| Peak ft | Back spin | Total spin | Path | Face to path | Face to target |"
    )
    assert header in sim
    assert (
        "| swing-01 | 73.2 | 89.7 | 1.23 | 110.0 | 109.8 | 121.4 | 33.8 | 3365 | 3554 "
        "| 1.0 I-O | 5.5 C | 4.6 O |" in sim
    )
    assert (
        "| swing-02 | 73.2 | 89.7 | 1.23 | 110.0 | | 121.4 | 33.8 | 3365 | 3554 "
        "| 1.0 I-O | 5.5 C | |" in sim
    )
    assert "| swing-03 | | | | | | | | | | | | |" in sim
    assert text.index("| swing-01 | 24.40s") < text.index("## Sim") < text.index("## Timing")


def test_session_md_without_a_screen_has_no_sim_table(tmp_path):
    write_session_md(tmp_path / "session.md", summary(swings=[Swing(12.4, 1.0, 16.0, None)]))
    text = (tmp_path / "session.md").read_text()
    assert "## Sim" not in text and "sim.json" not in text


def test_the_reading_guide_names_sim_json_when_the_screen_is_read(tmp_path):
    shot = SimShot(dict.fromkeys(FIELDS), dict.fromkeys(FIELDS, 0), 20, {})
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings, sims=[shot]))
    guide = (tmp_path / "session.md").read_text().split("## Sim")[0]
    assert "`sim.json`" in guide
    after_shot = next(line for line in guide.splitlines() if "`07-after-shot.jpg`" in line)
    assert "blank" in after_shot


def test_session_md_with_no_swings_says_so(tmp_path):
    path = tmp_path / "session.md"
    write_session_md(path, summary())
    assert "No swings detected." in path.read_text()
    assert "Rejected" not in path.read_text()
    assert "How to read" not in path.read_text()
    assert "## Timing" not in path.read_text()


@pytest.mark.parametrize(
    "t, text",
    [
        (4.369, "4.37s"),
        (59.99, "59.99s"),
        (59.996, "1:00.0"),
        (60.0, "1:00.0"),
        (727.4, "12:07.4"),
        (3725.04, "62:05.0"),
    ],
)
def test_video_time_switches_to_minutes_at_a_minute(t, text):
    assert video_time(t) == text


def test_video_time_rounds_into_the_next_minute():
    assert video_time(119.96) == "2:00.0"


def test_session_md_shows_long_video_times_in_minutes(tmp_path):
    path = tmp_path / "session.md"
    write_session_md(
        path,
        summary(
            swings=[Swing(727.4, 1.0, 16.0, swing_track(727.4))],
            rejected=[Rejected(61.2, 0.4, "too slow")],
        ),
    )
    text = path.read_text()
    assert "| swing-01 | 12:07.4 |" in text
    assert "| 1:01.2 |" in text


def _guide_summary(storage):
    return summary(
        swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))],
        balls=[BallFlight("ok", ball_px_per_frame=14.4, start_dir=-0.36, grade="good")],
        ball_median=14.4,
        storage=storage,
    )


def test_github_session_md_does_not_mention_clips_or_small_copies(tmp_path):
    write_session_md(tmp_path / "session.md", _guide_summary("github"))
    text = (tmp_path / "session.md").read_text()
    assert "clip.mp4" not in text and "-small.jpg" not in text and "Drive connector" not in text
    assert "the frame number in the swing" in text


def test_drive_session_md_keeps_the_drive_guide(tmp_path):
    write_session_md(tmp_path / "session.md", _guide_summary("drive"))
    text = (tmp_path / "session.md").read_text()
    assert "06-sequence-small.jpg" in text and "`clip.mp4` is the swing as video." in text


def test_session_md_names_the_venue(tmp_path):
    write_session_md(tmp_path / "session.md", summary(venue="sim"))
    lines = (tmp_path / "session.md").read_text().splitlines()
    assert lines.index("- Venue: sim") == lines.index("- Swings: 0") + 1


def test_session_md_and_summary_name_the_club(tmp_path):
    write_session_md(tmp_path / "session.md", summary(club="mixed"))
    lines = (tmp_path / "session.md").read_text().splitlines()
    assert lines.index("- Club: mixed") == lines.index("- Venue: range") + 1
    assert session_summary(summary())["club"] == "7 iron"


def test_session_md_with_no_swings_is_a_range_session(tmp_path):
    write_session_md(tmp_path / "session.md", summary())
    assert "- Venue: range" in (tmp_path / "session.md").read_text()


def test_the_reading_guide_explains_the_venue(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings, venue="sim"))
    text = (tmp_path / "session.md").read_text()
    assert "Venue is sim when the simulator's readout panel was found in the video" in text


def test_session_md_summary_line_follows_the_grade_basis(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4)), Swing(40.0, 0.9, 10.0, None)]
    lost = BallFlight("lost: ball lost after launch", grade="poor")
    balls = [BallFlight("ok", grade="good"), lost]
    write_session_md(
        tmp_path / "session.md",
        summary(swings=swings, balls=balls, ball_median=1.35, grade_basis="sim_smash"),
    )
    text = (tmp_path / "session.md").read_text()
    assert "Ball flight: 1 good, 0 fair, 1 poor, 0 ungraded" in text
    assert "(graded by sim smash factor, median 1.35)" in text
    assert "| poor | lost: ball lost after launch |" in text


def test_the_reading_guide_explains_smash_grading(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings, balls=[BallFlight("ok")]))
    text = (tmp_path / "session.md").read_text()
    assert "Grade comes from the sim's smash factor against the session median" in text


def test_the_reading_guide_describes_the_takeaway_at_hip_height(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings))
    text = (tmp_path / "session.md").read_text()
    assert "Takeaway is the hands reaching hip height going back" in text


def test_the_reading_guide_lists_the_elbows(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings))
    text = (tmp_path / "session.md").read_text()
    assert "shoulders, elbows, wrists, hips, knees, and heels" in text


def test_the_reading_guide_says_what_a_lost_track_means_on_each_basis(tmp_path):
    swings = [Swing(12.4, 1.0, 16.0, swing_track(12.4))]
    write_session_md(tmp_path / "session.md", summary(swings=swings, balls=[BallFlight("ok")]))
    text = (tmp_path / "session.md").read_text()
    assert "followed, and that swing has no grade" not in text
    assert "on the px basis that swing has no grade" in text
    assert "on the smash basis its grade still comes from the sim" in text


def test_timing_row_marks_a_start_timed_off_the_hands(tmp_path):
    path = tmp_path / "session.md"
    timing = Timing(11.2, 12.0, 12.4, 144, 240, 288)
    write_session_md(
        path, summary(swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))], timings=[timing])
    )
    assert "| swing-01 | 144 (11.20s) hands | 240 (12.00s) |" in path.read_text()


def _graded(grades: list[str | None], **kw) -> SessionSummary:
    """A session with one swing per grade, at 10s, 20s and so on."""
    swings = [Swing(10.0 * n, 1.0, 16.0, swing_track(10.0 * n)) for n in range(1, len(grades) + 1)]
    balls = [BallFlight("ok", grade=g) for g in grades]
    return summary(swings=swings, balls=balls, **kw)


def _shot(**values: str) -> SimShot:
    return SimShot(dict.fromkeys(FIELDS) | values, dict.fromkeys(FIELDS, 3), 6, {})


def _body(**values: float | str) -> dict:
    return dict.fromkeys(BODY_COLUMNS.values()) | {"view": "down the line"} | values


def test_session_md_has_a_body_table(tmp_path):
    path = tmp_path / "session.md"
    bodies = [_body(head_impact_up=-0.04, hands_impact_side=0.126, spine_impact=31.26), _body()]
    write_session_md(path, _graded(["good", "good"], bodies=bodies))
    text = path.read_text()
    assert "- The Body table measures the pose" in text
    body = text.split("## Body")[1]
    assert "| Swing | View | Head top side | Head top up |" in body
    assert "| swing-01 | down the line | | | | -0.04 | | +0.13 | | | | 31.3 |" in body
    assert "| swing-02 | down the line | | | | | | | | | | |" in body
    assert "## By grade" not in text  # one grade has nothing to compare against


def test_session_md_averages_each_number_by_grade(tmp_path):
    path = tmp_path / "session.md"
    sims = [
        _shot(club_path="3.0 I-O", face_to_path="2.0 C", smash="1.40"),
        _shot(club_path="1.0 O-I", face_to_path="1.0 O", smash="1.30"),
        _shot(club_path="4.0 I-O", smash="1.10"),
        None,
    ]
    timings = [
        Timing(9.2, 9.7, 10.0, 0, 0, 0, start_from_club=True),
        Timing(19.3, 19.7, 20.0, 0, 0, 0, start_from_club=True),
        Timing(29.5, 29.7, 30.0, 0, 0, 0),  # timed off the hands: left out
        Timing(39.1, 39.7, 40.0, 0, 0, 0, top_in_frame=False, start_from_club=True),
    ]
    bodies = [_body(hands_impact_up=0.1), _body(hands_impact_up=0.2), _body(), _body()]
    grades = ["good", "good", "poor", None]
    write_session_md(path, _graded(grades, sims=sims, timings=timings, bodies=bodies))
    table = path.read_text().split("## By grade")[1].split("## ")[0]
    assert "| Measure | good (2) | poor (1) |" in table
    assert "| Smash | 1.35 | 1.10 |" in table
    assert "| Path | 1.0 I-O | 4.0 I-O |" in table
    assert "| Face to path | 0.5 C |  |" in table
    assert "| Backswing s | 0.45 |  |" in table
    assert "| Hands impact up | +0.15 |  |" in table
    assert "Carry raw" not in table  # no swing has it


def test_session_summary_has_grade_counts_and_medians():
    sims = [_shot(smash="1.40", club_path="3.0 I-O"), _shot(smash="1.30", club_path="1.0 O-I")]
    sims += [_shot(smash="1.10"), None]
    row = session_summary(_graded(["good", "good", "poor", None], sims=sims, venue="sim"))
    assert row["grades"] == {"good": 2, "fair": 0, "poor": 1}
    assert (row["venue"], row["swings"]) == ("sim", 4)
    assert row["medians"] == {"smash": 1.3, "club_path": 1.0}


def test_index_skips_a_folder_that_is_not_a_session(tmp_path):
    (tmp_path / "notes").mkdir()
    write_index(tmp_path, ".golf-etl.json")
    assert "notes" not in (tmp_path / "index.md").read_text()


def test_session_summary_keeps_every_swing_number():
    sims = [_shot(smash="1.40", club_path="3.0 I-O"), None]
    bodies = [_body(spine_address=36.81234), _body(view="face-on")]
    summary = _graded(["good", None], sims=sims, bodies=bodies, strikes=[1.23456, None])
    rows = session_summary(summary)["per_swing"]
    assert (
        rows[0]["swing"] == 1 and rows[0]["grade"] == "good" and rows[0]["view"] == "down the line"
    )
    assert (rows[0]["smash"], rows[0]["club_path"], rows[0]["spine_address"]) == (1.4, 3.0, 36.8123)
    assert rows[1]["grade"] is None and rows[1]["view"] == "face-on" and rows[1]["smash"] is None
    assert rows[0]["strike"] == 1.2346 and rows[1]["strike"] is None
