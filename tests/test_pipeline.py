from datetime import UTC, datetime

import pytest
from PIL import Image

from golf_etl.config import Settings
from golf_etl.detect import Detection, Swing
from golf_etl.pipeline import process_video
from tests.conftest import make_video, make_video_with_clicks
from tests.poses import swing_track

pytestmark = pytest.mark.media
UPLOADED = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
EXPECTED = [
    "01-address.jpg",
    "02-top.jpg",
    "03-impact.jpg",
    "04-finish.jpg",
    "05-impact-zoom.jpg",
    "06-sequence.jpg",
    "07-after-shot.jpg",
    "clip.mp4",
]


def test_swing_folder_has_every_file_sized_for_claude(tmp_path):
    video = make_video_with_clicks(tmp_path / "range.mp4", [4.0], seconds=8.0, size="3840x2160")
    fake = lambda v, info, cfg: Detection([Swing(4.0, 1.0, 10.0, swing_track(4.0, fps=30))], [])  # noqa: E731
    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="range.mp4",
        checksum="ab12cd34ef567890",
        uploaded_at=UPLOADED,
        detect=fake,
    )
    swing = result.session_dir / "swing-01"
    assert sorted(p.name for p in swing.iterdir()) == EXPECTED
    for name in EXPECTED[:-1]:
        with Image.open(swing / name) as img:
            assert max(img.size) <= 2560
            assert img.size[0] * img.size[1] <= 3_700_000
            assert img.mode == "RGB"
    with Image.open(swing / "01-address.jpg") as img:
        assert img.size == (2560, 1440)
    with Image.open(swing / "05-impact-zoom.jpg") as img:
        assert img.size == (2433, 1520)  # three 2592x540 native bands, fit to the pixel budget
    with Image.open(swing / "06-sequence.jpg") as img:
        assert img.size == (2560, 720)  # two rows of four 640x360 cells
    assert result.swings == 1


def test_video_without_a_person_writes_a_zero_swing_session(tmp_path):
    video = make_video_with_clicks(tmp_path / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="empty.mp4",
        checksum="ffff0000ffff0000",
        uploaded_at=UPLOADED,
    )
    assert result.swings == 0
    assert [r.reason for r in result.rejected] == ["no pose found"]
    assert result.session_id == "2026-10-02-0900-ffff0000"
    assert sorted(p.name for p in result.session_dir.iterdir()) == ["session.md"]


def test_video_without_audio_is_a_zero_swing_session(tmp_path):
    video = make_video(tmp_path / "muted.mp4", audio=False)
    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="muted.mp4",
        checksum="0123456789abcdef",
        uploaded_at=UPLOADED,
    )
    assert result.swings == 0 and result.rejected == []


def test_swing_at_the_edges_of_the_clip_still_writes_every_file(tmp_path):
    video = make_video_with_clicks(tmp_path / "edge.mp4", [0.6], seconds=2.0, size="640x360")
    track = swing_track(0.6, fps=30, before=0.6, after=1.3)

    def fake(v, info, cfg):
        return Detection([Swing(0.6, 1.0, 0.0, track)], [])

    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="edge.mp4",
        checksum="0123456789abcdef",
        uploaded_at=UPLOADED,
        detect=fake,
    )
    assert sorted(p.name for p in (result.session_dir / "swing-01").iterdir()) == EXPECTED


def test_file_that_is_not_a_video_raises(tmp_path):
    bogus = tmp_path / "notes.mov"
    bogus.write_text("not a video")
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        process_video(
            bogus,
            tmp_path / "out",
            Settings(),
            source_name="notes.mov",
            checksum="0123456789abcdef",
            uploaded_at=UPLOADED,
        )
