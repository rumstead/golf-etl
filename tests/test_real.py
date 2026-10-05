"""Real footage in local/ (gitignored). Skipped when local/labels.yaml is missing, as in CI."""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from golf_etl.config import Settings
from golf_etl.eval import detected_impacts, run
from golf_etl.pipeline import process_video

LOCAL = Path(os.environ.get("GOLF_LOCAL_DIR", Path(__file__).parent.parent / "local"))
LABELS = LOCAL / "labels.yaml"

pytestmark = [
    pytest.mark.real,
    pytest.mark.skipif(not LABELS.exists(), reason="no local/labels.yaml"),
]


def test_detection_matches_the_labels():
    total, lines = run(LABELS, Settings(), detected_impacts)
    print("\n".join(lines))
    assert total.precision == 1.0
    assert total.recall == 1.0


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
