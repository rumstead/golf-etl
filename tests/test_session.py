from datetime import UTC, datetime

from golf_etl.ball import BallFlight
from golf_etl.detect import Rejected, Swing
from golf_etl.session import SessionSummary, session_id, write_session_md
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
    assert "| swing-01 | 65.81s | 0.45 | +50ms | 14.4 | -0.36 | poor | ok |" in text
    assert "| swing-02 | 914.32s | 0.38 | -83ms | | | | lost: no ball launched |" in text
    assert "Ball px/frame" in text.split("| Swing |")[0]  # the guide explains the columns


def test_session_md_with_no_swings_says_so(tmp_path):
    path = tmp_path / "session.md"
    write_session_md(path, summary())
    assert "No swings detected." in path.read_text()
    assert "Rejected" not in path.read_text()
    assert "How to read" not in path.read_text()
