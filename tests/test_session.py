import zipfile
from datetime import UTC, datetime

from golf_etl.ball import BallFlight
from golf_etl.detect import Rejected, Swing
from golf_etl.frames import Timing
from golf_etl.session import (
    ZIP_TEXT_MAX_BYTES,
    SessionSummary,
    session_id,
    write_session_md,
    write_session_zip,
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
    assert "| swing-01 | 65.81s | 0.45 | +50ms | 14.4 | -0.36 | poor | ok |" in text
    assert "| swing-02 | 914.32s | 0.38 | -83ms | | | | lost: no ball launched |" in text
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


def test_session_zip_has_the_summary_small_images_and_small_text_files(tmp_path):
    swing = tmp_path / "swing-01"
    swing.mkdir()
    (tmp_path / "session.md").write_text("# Session\n")
    (tmp_path / ".golf-etl.json").write_text("{}")
    for name in ("06-sequence-small.jpg", "01-address.jpg", "06-sequence.jpg", "clip.mp4"):
        (swing / name).write_bytes(b"\xff\xd8 not really an image")
    (swing / "pose.csv").write_text("t,x\n0,1\n")
    (swing / "ball.json").write_text("{}")
    (swing / "huge.csv").write_text("x" * (ZIP_TEXT_MAX_BYTES + 1))
    write_session_zip(tmp_path)
    with zipfile.ZipFile(tmp_path / "session.zip") as z:
        assert sorted(z.namelist()) == [
            "session.md",
            "swing-01/06-sequence-small.jpg",
            "swing-01/ball.json",
            "swing-01/pose.csv",
        ]
        assert z.read("swing-01/pose.csv") == b"t,x\n0,1\n"


def test_session_zip_replaces_an_older_copy_without_including_it(tmp_path):
    (tmp_path / "session.md").write_text("# Session\n")
    (tmp_path / "session.zip").write_bytes(b"stale")
    write_session_zip(tmp_path)
    with zipfile.ZipFile(tmp_path / "session.zip") as z:
        assert z.namelist() == ["session.md"]
