"""Real footage in local/ (gitignored). A test is skipped when its labels are missing, as in CI."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from golf_etl.config import Settings
from golf_etl.eval import detected_impacts, run
from golf_etl.pipeline import process_video
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
        assert result.swings == len(entry["impacts"])
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
    assert wrong == 0 and blank <= 1


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
