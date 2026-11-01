import json
import logging
import re
import subprocess
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from PIL import Image

from golf_etl.ball import on_rest_spot, track_ball
from golf_etl.config import Settings
from golf_etl.detect import Detection, Swing
from golf_etl.media import probe
from golf_etl.pipeline import _impact_frames, process_video
from golf_etl.pose import (
    LEFT_ANKLE,
    LEFT_FOOT_INDEX,
    LEFT_HEEL,
    RIGHT_ANKLE,
    RIGHT_FOOT_INDEX,
    RIGHT_HEEL,
)
from tests.ball_clips import address_pose, make_ball_clip
from tests.conftest import make_video, make_video_with_clicks
from tests.poses import swing_track
from tests.sim_fixtures import load

pytestmark = pytest.mark.media
UPLOADED = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
EXPECTED = [
    "05-impact-zoom-small.jpg",
    "05-impact-zoom.jpg",
    "06-sequence-small.jpg",
    "06-sequence.jpg",
    "07-after-shot-small.jpg",
    "07-after-shot.jpg",
    "ball.json",
    "clip.mp4",
    "pose.csv",
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
    for name in (n for n in EXPECTED if n.endswith("-small.jpg")):
        assert (swing / name).stat().st_size <= Settings().small_max_bytes
    for name in (n for n in EXPECTED if n.endswith(".jpg")):
        with Image.open(swing / name) as img:
            assert max(img.size) <= 2560
            assert img.size[0] * img.size[1] <= 3_700_000
            assert img.mode == "RGB"
    with Image.open(swing / "07-after-shot.jpg") as img:
        assert img.size == (2560, 1440)
    with Image.open(swing / "05-impact-zoom.jpg") as img:
        assert img.size == (2433, 1520)  # three 2592x540 native bands, fit to the pixel budget
    with Image.open(swing / "06-sequence.jpg") as img:
        assert img.size == (2560, 720)  # two rows of four 640x360 cells
    assert result.swings == 1
    # a test pattern has no ball, so the row says why instead of a grade
    ball = json.loads((swing / "ball.json").read_text())
    assert ball["track"].startswith("lost: ")
    row = next(
        r
        for r in (result.session_dir / "session.md").read_text().splitlines()
        if r.startswith("| swing-01")
    )
    assert row.endswith(f"| {ball['track']} |")
    assert (
        "Ball flight: 0 good, 0 fair, 0 poor, 1 not tracked"
        in (result.session_dir / "session.md").read_text()
    )
    # the clip starts at 1.5s (impact 4.0s minus 2.5s), so impact is clip frame 75 at 30fps
    assert "| swing-01 |" in (result.session_dir / "session.md").read_text().split("## Timing")[1]
    assert "| 75 (4.00s) |" in (result.session_dir / "session.md").read_text()


def test_impact_zoom_has_a_frame_on_each_side_of_impact(tmp_path):
    # at 29.97fps the frame nearest 4.01s falls at the edge of a three-frame window
    video = make_video_with_clicks(
        tmp_path / "ntsc.mp4", [4.01], seconds=14.0, size="640x360", rate="30000/1001"
    )
    fake = lambda v, info, cfg: Detection([Swing(4.01, 1.0, 10.0, swing_track(4.01, fps=30))], [])  # noqa: E731
    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="ntsc.mp4",
        checksum="ab12cd34ef567890",
        uploaded_at=UPLOADED,
        detect=fake,
    )
    with Image.open(result.session_dir / "swing-01" / "05-impact-zoom.jpg") as img:
        assert img.size == (432, 270)  # three 432x90 bands


def test_impact_is_the_last_frame_before_the_ball_leaves_its_spot(tmp_path):
    # the ball leaves its spot after frame 300 (2.5s); the sound lands three frames early
    video = make_ball_clip(tmp_path / "swing.mp4")
    heard = 2.5 - 3 / 120
    track = swing_track(heard, fps=30)
    feet = [LEFT_ANKLE, LEFT_HEEL, LEFT_FOOT_INDEX, RIGHT_ANKLE, RIGHT_HEEL, RIGHT_FOOT_INDEX]
    track.landmarks[:, feet] = address_pose()[feet]
    fake = lambda v, info, cfg: Detection([Swing(heard, 1.0, 10.0, track)], [])  # noqa: E731
    result = process_video(
        video,
        tmp_path / "out",
        Settings(),
        source_name="swing.mp4",
        checksum="ab12cd34ef567890",
        uploaded_at=UPLOADED,
        detect=fake,
    )
    timing = (result.session_dir / "session.md").read_text().split("## Timing")[1]
    row = next(r for r in timing.splitlines() if r.startswith("| swing-01"))
    impact_frame = int(re.findall(r"\| (\d+) \(", row)[2])  # the clip starts at 0s
    assert abs(impact_frame - 299) <= 1


def _impact_on_spot(video, pose, heard=2.5 - 3 / 120):
    """Whether the ball is on its rest spot in impact -1, impact and impact +1."""
    info = probe(video)
    rest = track_ball(video, 2.5, pose).rest
    frames, i, _ = _impact_frames(video, info, heard, rest)
    assert i >= 1
    return [on_rest_spot(img, rest) for _, img in frames[i - 1 : i + 2]]


def test_impact_face_on_is_the_last_frame_with_the_ball_on_its_spot(tmp_path):
    video = make_ball_clip(tmp_path / "swing.mp4", rest=(270, 640), velocity=(0.0, -18.0))
    pose = address_pose(ankle_x=(0.35, 0.65), toes=0, hip_y=0.37)
    assert _impact_on_spot(video, pose) == [True, True, False]


def test_impact_down_the_line_is_a_frame_before_the_ball_leaves_its_spot(tmp_path):
    # down the line the ball still covers its spot the frame after contact
    video = make_ball_clip(tmp_path / "swing.mp4")
    assert _impact_on_spot(video, address_pose()) == [True, True, True]


def test_impact_down_the_line_when_the_ball_leaves_early_in_the_search(tmp_path):
    # the sound 12.4 frames after the ball last sits on its spot (frame 300)
    video = make_ball_clip(tmp_path / "swing.mp4")
    assert _impact_on_spot(video, address_pose(), heard=312.4 / 120) == [True, True, True]


def test_impact_search_scales_with_the_frame_rate(tmp_path):
    # at 240fps the sound lands 15 frames early, still inside 0.1s
    video = make_ball_clip(tmp_path / "swing.mp4", fps=240, frames=960, launch_frame=600)
    rest = track_ball(video, 2.5, address_pose()).rest
    _, _, impact_s = _impact_frames(video, probe(video), 2.5 - 15 / 240, rest)
    assert impact_s == pytest.approx(2.5 - 1 / 240, abs=1.5 / 240)


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


def _process(video, out, swings):
    return process_video(
        video,
        out,
        Settings(),
        source_name=video.name,
        checksum="0123456789abcdef",
        uploaded_at=UPLOADED,
        detect=lambda v, info, cfg: Detection(
            [Swing(t, 1.0, 0.0, swing_track(t, fps=30, before=t)) for t in swings], []
        ),
    )


def test_a_video_without_a_screen_writes_no_sim_json(tmp_path, caplog):
    video = make_video_with_clicks(tmp_path / "range.mp4", [1.0], seconds=8.0, size="640x360")
    with caplog.at_level(logging.INFO, logger="golf_etl.pipeline"):
        result = _process(video, tmp_path / "out", [1.0])
    assert not (result.session_dir / "swing-01" / "sim.json").exists()
    assert "## Sim" not in (result.session_dir / "session.md").read_text()
    assert "no readout panel: 0 of 3 frames matched" in caplog.text


def _screen_still(name: str, down: int = 0) -> np.ndarray:
    """A 1920x1080 frame with a fixture's panel where IMG_4761 has it, or `down` px lower."""
    rgb, _ = load(name)
    # the fixture's panel at (40, 40) sits at (1381, 203) in the video
    still = np.empty((1080, 1920, 3), np.uint8)
    still[:] = load("4761-s1")[0][0, 0]
    still[163 + down : 163 + down + rgb.shape[0], 1341 : 1341 + rgb.shape[1]] = rgb
    return still


def _still_video(path, stills: list[tuple[np.ndarray, float]]):
    """A 10fps video showing each (frame, seconds) in turn."""
    inputs, streams = [], ""
    for k, (still, seconds) in enumerate(stills):
        png = path.with_name(f"{path.stem}-{k}.png")
        cv2.imwrite(str(png), cv2.cvtColor(still, cv2.COLOR_RGB2BGR))
        inputs += ["-loop", "1", "-framerate", "10", "-t", str(seconds), "-i", str(png)]
        streams += f"[{k}:v]"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex"]
        + [f"{streams}concat=n={len(stills)}:v=1[v]", "-map", "[v]", "-r", "10"]
        + ["-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def screen_video(tmp_path_factory):
    """16s of IMG_4762's readout panel for 3s, then IMG_4761's, where IMG_4761 has its panel."""
    folder = tmp_path_factory.mktemp("screen")
    stills = [(_screen_still("4762-s8"), 3.0), (_screen_still("4761-s1"), 13.0)]
    return _still_video(folder / "screen.mp4", stills)


def _sim(session_dir, n):
    return json.loads((session_dir / f"swing-{n:02d}" / "sim.json").read_text())


def test_a_video_with_a_screen_writes_sim_json(tmp_path, screen_video):
    result = _process(screen_video, tmp_path / "out", [1.0])
    sim = _sim(result.session_dir, 1)
    assert sim["fields"]["club_speed"]["text"] == "73.2"
    assert sim["unchanged"] is False
    text = (result.session_dir / "session.md").read_text()
    row = next(r for r in text.split("## Sim")[1].splitlines() if r.startswith("| swing-01"))
    assert row.startswith("| swing-01 | 73.2 |")


def test_a_swing_with_no_time_before_the_next_still_gets_a_blank_sim_json(tmp_path, screen_video):
    # the second swing's clip starts before the first swing's readout appears
    result = _process(screen_video, tmp_path / "out", [1.0, 3.0])
    first = _sim(result.session_dir, 1)
    assert first["frames"] == 0
    assert all(f["text"] is None for f in first["fields"].values())
    assert _sim(result.session_dir, 2)["fields"]["club_speed"]["text"] == "73.2"


def test_a_swing_with_the_same_numbers_on_screen_before_impact_reads_blank(tmp_path, screen_video):
    # IMG_4761's panel is up from 3s, so a swing at 6s left the screen as it was
    result = _process(screen_video, tmp_path / "out", [6.0])
    sim = _sim(result.session_dir, 1)
    assert sim["unchanged"] is True
    assert sim["frames"] > 0
    assert all(f["text"] is None for f in sim["fields"].values())
    text = (result.session_dir / "session.md").read_text()
    assert "| swing-01 | | | | | | | | | | | | |" in text.split("## Sim")[1]


def _moved_video(path, down: int):
    """Swings at 1, 9 and 17s; around swing 2 the camera put the panel `down` px lower."""
    stills = [
        (_screen_still("4762-s8"), 1.0),
        (_screen_still("4761-s1"), 6.0),
        (_screen_still("4762-s8", down), 2.0),
        (_screen_still("4761-s1", down), 6.0),
        (_screen_still("4762-s8"), 2.0),
        (_screen_still("4761-s1"), 15.0),
    ]
    return _still_video(path, stills)


def test_a_swing_whose_panel_moved_a_row_reads_blank(tmp_path, caplog):
    # the session box comes from swings 1 and 3
    video = _moved_video(tmp_path / "moved.mp4", 40)
    with caplog.at_level(logging.INFO, logger="golf_etl.pipeline"):
        result = _process(video, tmp_path / "out", [1.0, 9.0, 17.0])
    assert _sim(result.session_dir, 1)["fields"]["club_speed"]["text"] == "73.2"
    moved = _sim(result.session_dir, 2)
    assert all(f["text"] is None for f in moved["fields"].values())
    assert "swing-02: readout panel moved" in caplog.text
    assert _sim(result.session_dir, 3)["fields"]["club_speed"]["text"] == "73.2"


def test_a_swing_whose_panel_drifted_four_pixels_still_reads(tmp_path, caplog):
    video = _moved_video(tmp_path / "drift.mp4", 4)
    with caplog.at_level(logging.INFO, logger="golf_etl.pipeline"):
        result = _process(video, tmp_path / "out", [1.0, 9.0, 17.0])
    assert _sim(result.session_dir, 2)["fields"]["club_speed"]["text"] == "73.2"
    assert "panel moved" not in caplog.text


def test_a_swing_whose_checked_frames_find_no_panel_still_reads(tmp_path, caplog):
    # someone stands in front of the panel from 3.3 to 3.8s and 4.3 to 4.8s, when swing 1's
    # frames are searched for it, but not at 4, 5 and 6s, when the session's box is found
    covered = _screen_still("4761-s1")
    covered[180:470, 1360:1530] = (25, 25, 35)
    stills = [
        (_screen_still("4762-s8"), 1.0),
        (_screen_still("4761-s1"), 2.3),
        (covered, 0.5),
        (_screen_still("4761-s1"), 0.5),
        (covered, 0.5),
        (_screen_still("4761-s1"), 11.2),
    ]
    video = _still_video(tmp_path / "covered.mp4", stills)
    with caplog.at_level(logging.INFO, logger="golf_etl.pipeline"):
        result = _process(video, tmp_path / "out", [1.0])
    assert _sim(result.session_dir, 1)["fields"]["club_speed"]["text"] == "73.2"
    assert "panel moved" not in caplog.text


def test_a_swing_that_fails_to_read_gets_no_sim_json(tmp_path, screen_video, monkeypatch, caplog):
    from golf_etl import pipeline

    def broken(frames, panel, glyphs, before=(), settled=True):
        raise ValueError("unreadable")

    monkeypatch.setattr(pipeline, "read_shot", broken)
    with caplog.at_level(logging.INFO, logger="golf_etl.pipeline"):
        result = _process(screen_video, tmp_path / "out", [1.0])
    assert "ValueError: unreadable" in caplog.text
    assert not (result.session_dir / "swing-01" / "sim.json").exists()
    text = (result.session_dir / "session.md").read_text()
    assert "| swing-01 | | | | | | | | | | | | |" in text.split("## Sim")[1]


def test_a_panel_search_that_fails_still_writes_the_session(tmp_path, screen_video, monkeypatch):
    from golf_etl import pipeline

    def broken(frame):
        raise ValueError("no edges")

    monkeypatch.setattr(pipeline, "find_panel", broken)
    result = _process(screen_video, tmp_path / "out", [1.0])
    swing = result.session_dir / "swing-01"
    assert sorted(p.name for p in swing.iterdir()) == EXPECTED
    text = (result.session_dir / "session.md").read_text()
    assert "## Sim" not in text and "## Timing" in text


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
