from datetime import UTC, datetime

import pytest

from golf_etl.ball import BallFlight
from golf_etl.detect import Rejected, Swing
from golf_etl.frames import Timing
from golf_etl.session import (
    SessionSummary,
    session_id,
    video_time,
    write_session_md,
)
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
    write_session_md(path, summary(swings=swings, balls=balls, ball_median=21.3))
    text = path.read_text()
    assert (
        "Ball flight: 0 good, 0 fair, 1 poor, 1 not tracked (median ball speed 21.3 px/frame)"
        in text
    )
    header = "| Swing | Impact | Strength | Hand speed peak | Ball px/frame | Start dir | Grade |"
    assert f"{header} Track |" in text
    assert "| swing-01 | 1:05.8 | 0.45 | +50ms | 14.4 | -0.36 | poor | ok |" in text
    assert "| swing-02 | 15:14.3 | 0.38 | -83ms | | | | lost: no ball launched |" in text
    assert "Ball px/frame" in text.split("| Swing |")[0]  # the guide explains the columns


def test_session_md_has_a_timing_table(tmp_path):
    path = tmp_path / "session.md"
    timing = Timing(
        takeaway_start_s=11.2,
        top_s=12.0,
        impact_s=12.4,
        takeaway_start_frame=144,
        top_frame=240,
        impact_frame=288,
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
    )
    write_session_md(
        path, summary(swings=[Swing(12.4, 1.0, 16.0, swing_track(12.4))], timings=[timing])
    )
    row = "| swing-01 | 144 (11.20s) | hands not seen at the top | 288 (12.40s) | | | |"
    assert row in path.read_text()


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
