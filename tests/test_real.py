"""Real footage in local/ (gitignored). A test is skipped when its labels are missing, as in CI."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from golf_etl.config import Settings
from golf_etl.eval import detected_impacts, run
from golf_etl.frames import pick_positions
from golf_etl.media import probe
from golf_etl.pipeline import detect_video, process_video
from golf_etl.sim import PANEL_REFERENCE, Glyphs
from golf_etl.sim_eval import held_out, score

LOCAL = Path(os.environ.get("GOLF_LOCAL_DIR", Path(__file__).parent.parent / "local"))
LABELS = LOCAL / "labels.yaml"
SIM_LABELS = LOCAL / "sim_labels.yaml"
GLYPHS = PANEL_REFERENCE.with_name("sim_glyphs.npz")

pytestmark = pytest.mark.real
needs_labels = pytest.mark.skipif(not LABELS.exists(), reason="no local/labels.yaml")
needs_sim_labels = pytest.mark.skipif(not SIM_LABELS.exists(), reason="no local/sim_labels.yaml")


@needs_labels
def test_detection_matches_the_labels():
    total, lines = run(LABELS, Settings(), detected_impacts)
    print("\n".join(lines))
    assert total.precision == 1.0
    assert total.recall == 1.0


@needs_labels
def test_every_labeled_video_processes_into_claude_sized_frames(tmp_path):
    import yaml

    videos = yaml.safe_load(LABELS.read_text())["videos"]
    sessions = set()
    for entry in videos:
        video = LOCAL / entry["path"]
        with video.open("rb") as fh:
            checksum = hashlib.file_digest(fh, "sha256").hexdigest()
        result = process_video(
            video,
            tmp_path,
            Settings(),
            source_name=video.name,
            checksum=checksum,
            uploaded_at=datetime.now(UTC),
        )
        # no_ball motions may or may not be found; test_detection_matches_the_labels scores them
        found = len(entry["impacts"])
        assert found <= result.swings <= found + len(entry.get("no_ball", [])), entry["path"]
        sessions.add(result.session_id)
        for jpg in result.session_dir.glob("swing-*/*.jpg"):
            with Image.open(jpg) as img:
                assert max(img.size) <= 2576, jpg.name
                assert img.size[0] * img.size[1] <= 3_750_000, jpg.name
    assert len(sessions) == len(videos)  # same capture minute, different content


@needs_sim_labels
def test_sim_reads_held_out_videos_without_a_wrong_number():
    right, wrong, blank, lines = score(SIM_LABELS, held_out(SIM_LABELS))
    print("\n".join(lines))
    assert wrong == 0
    assert right >= 0.95 * (right + wrong + blank)


@needs_sim_labels
def test_committed_sim_glyphs_read_labeled_fields_with_no_wrong_number():
    glyphs = Glyphs.load(GLYPHS)
    right, wrong, blank, lines = score(SIM_LABELS, lambda video: glyphs)
    print("\n".join(lines))
    # IMG_4762's carry 178.8 at 61.1s and spin 2284 at 101.3s read in only a frame or two
    assert wrong == 0 and blank <= 2


@pytest.fixture(scope="module")
def session_4859(tmp_path_factory):
    """The 2026-11-08 sim session (IMG_4859.mov) processed once for the tests that read it."""
    video = LOCAL / "IMG_4859.mov"
    if not video.exists():
        pytest.skip("no local/IMG_4859.mov")
    return process_video(
        video,
        tmp_path_factory.mktemp("4859"),
        Settings(),
        source_name=video.name,
        checksum="86c76b86",
        uploaded_at=datetime.now(UTC),
    )


def _grade_rows(text: str) -> dict[str, str]:
    """Each swing's Grade cell from the session.md swing table."""
    rows = [r for r in text.split("## Sim")[0].splitlines() if r.startswith("| swing-")]
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    return {c[0]: c[6] for c in cells}


@needs_sim_labels
def test_the_sim_session_grades_by_smash(session_4859):
    text = (session_4859.session_dir / "session.md").read_text()
    assert "graded by sim smash factor" in text
    grades = _grade_rows(text)
    print(grades)
    assert grades["swing-01"] == "poor" and grades["swing-08"] == "poor"
    assert all(grades[f"swing-{n:02d}"] == "good" for n in range(9, 16))
    assert all(grades[f"swing-{n:02d}"] == "" for n in (2, 5, 6))


def _video_seconds(text: str) -> float:
    """Seconds from a video time as session.md shows it: 24.45s or 1:05.8."""
    minutes, _, seconds = text.removesuffix("s").rpartition(":")
    return int(minutes or 0) * 60 + float(seconds)


@needs_sim_labels
def test_sim_session_matches_the_labels(tmp_path):
    import yaml

    videos = yaml.safe_load(SIM_LABELS.read_text())["videos"]
    entry = next(v for v in videos if v["path"] == "IMG_4761.MOV")
    video = LOCAL / entry["path"]
    result = process_video(
        video,
        tmp_path,
        Settings(),
        source_name=video.name,
        checksum="4761",
        uploaded_at=datetime.now(UTC),
    )
    text = (result.session_dir / "session.md").read_text()
    assert "## Sim" in text, "no readout panel found"
    print(text.split("## Sim")[1].split("## Timing")[0])
    swing_rows = text.split("## Sim")[0].splitlines()
    impacts = {
        row.split(" | ")[0].removeprefix("| "): _video_seconds(row.split(" | ")[1])
        for row in swing_rows
        if row.startswith("| swing-")
    }
    wrong = blank = 0
    for shot in entry["shots"]:
        impact = float(shot.pop("impact"))
        swing = next((s for s, t in impacts.items() if abs(t - impact) <= 1.0), None)
        assert swing, f"no swing within 1s of the shot at {impact}s"
        fields = json.loads((result.session_dir / swing / "sim.json").read_text())["fields"]
        print(f"{impact}s is {swing} at {impacts[swing]}s")
        for field, label in shot.items():
            read = fields[field]["text"]
            if read != str(label):
                print(f"  {field}: read {read}, label {label}")
                wrong += read is not None
                blank += read is None
    assert wrong == 0 and blank <= 1


@needs_sim_labels
def test_takeaway_hands_are_at_the_hips_on_the_labeled_sim_shots():
    import yaml

    video = LOCAL / "IMG_4859.mov"
    if not video.exists():
        pytest.skip("no local/IMG_4859.mov")
    videos = yaml.safe_load(SIM_LABELS.read_text())["videos"]
    shots = [float(s["impact"]) for v in videos if v["path"] == video.name for s in v["shots"]]
    cfg = Settings()
    detection = detect_video(video, probe(video), cfg)
    heights = {}
    for shot in shots:
        swing = next((s for s in detection.swings if abs(s.impact_s - shot) <= 1.0), None)
        assert swing is not None, f"no swing within 1s of the shot at {shot}s"
        p = pick_positions(
            swing.track,
            swing.impact_s,
            cfg.still_speed,
            cfg.transition_s,
            cfg.follow_through_s,
            cfg.takeaway_before_top_s,
        )
        i = int(np.argmin(np.abs(swing.track.times - p.takeaway_s)))
        below = swing.track.hands_below_hips()[i] / swing.track.leg_length()
        heights[shot] = round(float(below), 3)
    print(heights)
    assert len(heights) == len(shots) == 12
    # The takeaway is the first sample at or above the hip line, so this checks that the rule
    # fired on every registered shot and that no sample jumped far past the line, not where
    # the pose put the hips.
    assert all(-0.15 <= h <= 0.05 for h in heights.values())


@needs_sim_labels
def test_the_sim_session_keeps_the_shots_that_changed_the_screen():
    import yaml

    video = LOCAL / "IMG_4859.mov"
    if not video.exists():
        pytest.skip("no local/IMG_4859.mov")
    videos = yaml.safe_load(SIM_LABELS.read_text())["videos"]
    shots = [float(s["impact"]) for v in videos if v["path"] == video.name for s in v["shots"]]
    detection = detect_video(video, probe(video), Settings())
    impacts = [s.impact_s for s in detection.swings]
    print(sorted(round(t, 2) for t in impacts))
    print({round(r.time_s, 1): r.reason for r in detection.rejected})

    def found(t: float) -> bool:
        return any(abs(impact - t) <= 0.5 for impact in impacts)

    assert all(found(shot) for shot in shots)  # the 12 registered shots stay
    # registered on the sim, rejected before as within 8s of faster hands
    assert found(36.07) and found(160.9)
    # launched no ball right after a real shot; the screen did not change for them
    assert not found(39.36) and not found(168.5)
