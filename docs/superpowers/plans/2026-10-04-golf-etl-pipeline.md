# golf-etl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn swing videos dropped in a Google Drive folder into per-swing, Claude-sized frames written back to Drive, on a schedule, with checksum idempotency and bounded Drive usage.

**Architecture:** A Python CLI (`golf-etl`) in one container image. `process` turns one local video into a session folder (audio onsets, pose-confirmed swings, frame picks, rendering, clip). `poll-drive` wraps that in a Drive poller (recover, dedupe by checksum, claim, process, atomic publish, retries) and a retention sweep. Drive sits behind a small protocol so every idempotency and failure rule is tested against an in-memory fake.

**Tech Stack:** Python 3.12, ffmpeg 7 (Debian trixie), librosa 1.0, scipy, MediaPipe 1.0 Pose Landmarker (lite), OpenCV 5 (contrib, pulled in by mediapipe), Pillow, google-api-python-client, pytest, ruff, podman or docker.

**Spec:** [docs/design.md](../../design.md). Deployment is the separate homelab OpenSpec change `add-golf-etl` (rumstead/homelab#14).

## Global Constraints

- Python 3.12 (`python:3.12-slim`). Runtime pins are exact in `requirements.txt`; dev pins in `requirements-dev.txt`.
- Everything runs and is tested inside the image (`Dockerfile` `test` stage). The image needs `ffmpeg libgl1 libegl1 libgles2 libglib2.0-0`; MediaPipe fails to load without `libEGL`.
- Pose model: `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task` at `/opt/golf-etl/pose_landmarker_lite.task`.
- Every JPEG is sRGB, quality 90, no EXIF, at most 2560px on the long edge and 3.7MP (`frame_max_pixels`), never upscaled.
- Clip: impact minus 2.5s to plus 1.5s, 1080p long edge, H.264 CRF 23, AAC.
- Detection defaults: high-pass 2000Hz, onset delta 0.2, min gap 8s, audio lag 12ms, pose window plus or minus 1.5s, confirm window 300ms, min hand speed 1.0 frame heights/s, short clip 15s.
- Drive: root folder `golf` with `inbox/ processing/ failed/ sessions/`. Identity is `sha256Checksum`, falling back to `md5Checksum`. `appProperties` keys: `golfEtl`, `kind`, `sessionFolder`, `sha256`, `sourceName`, `pipelineVersion`, `attempts`. Deletes are permanent (`files.delete`), never trash.
- Limits: 3 attempts, failed TTL 3 days, clip TTL 14 days, session TTL 60 days, session size cap 3GiB (sessions only).
- Every setting is overridable as `GOLF_<FIELD>`; Drive credentials are `GOLF_DRIVE_CLIENT_ID`, `GOLF_DRIVE_CLIENT_SECRET`, `GOLF_DRIVE_REFRESH_TOKEN`.
- Image: `ghcr.io/rumstead/golf-etl:latest`, pushed on `main`, no version tags.
- No em or en dashes in code, comments, or commit messages. Commits are a subject line plus `git commit -s` sign-off, no body.

## Review Focus

- A video with no decodable audio (muted, screen recording) must produce a zero-swing session, not a failure. Pinned by `test_video_without_audio_is_a_zero_swing_session` (Task 9).
- A swing within 2.5s of the start or 1.5s of the end must still write every file from the clamped window. Pinned by `test_swing_at_the_edges_of_the_clip_still_writes_every_file` (Task 9).
- A loud swing closer than 8s to a louder sound must show up in `session.md` as rejected, not vanish. Pinned by `test_weaker_onset_within_min_gap_is_dropped` (Task 4) and `test_loud_onset_dropped_by_the_gap_is_reported` (Task 6).
- A file that is not really a video (renamed, truncated) must raise inside `process` so the poller retries it, and the other videos in that poll must still publish. Pinned by `test_file_that_is_not_a_video_raises` (Task 9) and `test_one_bad_video_does_not_stop_the_others` (Task 11).
- Filenames with spaces, quotes, and non-ASCII must survive scratch paths, error files, and Drive queries. Pinned by `test_awkward_filenames_survive_the_round_trip` (Task 11) and `test_find_by_property_quotes_values` (Task 13).

Known gaps no test covers, for the reviewer to weigh: a neighbor who is larger in frame than the golfer gets tracked instead (largest pose wins), and frame timestamps assume a constant frame rate (iPhone slo-mo is close, about 117 to 119fps).

## Real footage

Two clips from the owner's phone are already in `local/` (gitignored) with hand-labeled impact times in `local/labels.yaml`. They are 1080p portrait HEVC, 120fps slo-mo, Dolby Vision HLG, with an undecodable APAC audio track ahead of the AAC one. Both are named `IMG_4439` on the phone but have different content and the same capture minute. During planning the finished pipeline found one swing in each with impact within 1ms of the label. `pytest -m real` (Task 10) keeps it that way.

## File Structure

```
golf-etl/
  Dockerfile               base (deps + model + package), test (ruff + pytest), runtime
  pyproject.toml           package, entry point, pytest and ruff config
  requirements.txt         runtime pins (Renovate pip_requirements)
  requirements-dev.txt     pytest, ruff
  renovate.json5           pip, Dockerfile, GitHub Actions
  .github/workflows/ci.yaml
  README.md
  local/                   gitignored real footage + labels.yaml
  src/golf_etl/
    config.py              Settings dataclass, GOLF_* overrides
    media.py               ffprobe/ffmpeg: probe, audio, frames, clip; HDR tone map; audio track pick
    audio.py               high-passed onsets, backtracked, lag-corrected, min-gap suppression
    pose.py                PoseTrack (hands speed/height) and PoseEstimator (MediaPipe)
    detect.py              confirm onsets with a hand speed peak; Detection/Swing/Rejected
    frames.py              address/top/impact/finish picks, 8-position sequence; sharpest frame
    render.py              fit, label, skeleton, sequence grid, impact zoom band, JPEG save
    session.py             session id and session.md (with the how-to-read guide)
    pipeline.py            detect_video, process_video, write_swing
    eval.py                score detections against labels
    cli.py                 process, eval, poll-drive, auth
  claude/skills/golf-swing-analysis/SKILL.md   claude.ai coaching skill (uploaded by hand)
    drive/
      client.py            Drive protocol, DriveFile, Folders
      poller.py            Poller: recover, dedupe, claim, process, publish, retry
      retention.py         sweep: TTLs and session size cap
      google.py            GoogleDrive (Drive v3)
  tests/
    conftest.py            synthetic video helpers (ffmpeg lavfi)
    poses.py               synthetic pose tracks
    fake_drive.py          in-memory Drive
    test_*.py
```

## Working in the container

Branch first: `git switch -c feat/pipeline` from `main` once rumstead/golf-etl#1 is merged (or from `docs/design` if it is still open).

After Task 2 builds `golf-etl:test`, define a helper for the rest of the plan. It mounts the working tree over the installed package, and `pythonpath = ["src"]` in `pyproject.toml` makes `src/` win:

```sh
t() { podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test "$@"; }
```

Rebuild `golf-etl:test` only when `requirements*.txt` or the `Dockerfile` change. `docker` works the same as `podman`.

---

### Task 1: Spike: can claude.ai read JPEGs through the Drive connector?

The whole design leaves coaching to claude.ai reading frames from Drive. Check that before writing code.

**Files:** none

- [ ] **Step 1: Put a test image in Drive**

Take any photo of a person, about 2560px on the long edge (a phone photo resized is fine), and upload it to a new Drive folder `golf-spike/` as `test.jpg`.

- [ ] **Step 2: Ask claude.ai about it**

In a new claude.ai chat with the Google Drive connector enabled, send: `Open golf-spike/test.jpg from my Google Drive and describe the person's posture in detail.`

Expected: a description of what is actually in the photo (stance, arms, clothing). Fail: Claude says it can only see the file name or metadata, or cannot open images.

- [ ] **Step 3: Record the result**

Pass: delete `golf-spike/` and continue. Fail: stop and raise it with the owner; the fallback in docs/design.md is calling the Claude API from the pipeline, which changes Tasks 9 and 11.

---

### Task 2: Scaffold the package, image, and settings

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.gitignore`
- Create: `src/golf_etl/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_config.py`
- Create: `src/golf_etl/config.py`

**Interfaces:**
- Produces: `golf_etl.config.Settings` (frozen dataclass, fields and defaults below) and `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings`, which reads `GOLF_<FIELD>` and casts to the field's type, raising `ValueError` naming the variable on a bad value. Every later task takes a `Settings` as `cfg`.
- Produces: images `golf-etl:test` (lint + tests) and `golf-etl` (runtime, `ENTRYPOINT ["golf-etl"]`, `CMD ["poll-drive"]`, user 10001, `/scratch`, `HOME=/tmp`).

- [ ] **Step 1: Write the build files**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=80"]
build-backend = "setuptools.build_meta"

[project]
name = "golf-etl"
version = "0.1.0"
description = "Break down golf swing videos into images for AI analysis"
requires-python = ">=3.12"

[project.scripts]
golf-etl = "golf_etl.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "media: needs ffmpeg and the pose model (runs in the container)",
    "real: real footage from local/, skipped when it is missing",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

`requirements.txt`:

```text
google-api-python-client==2.201.0
google-auth==2.59.1
google-auth-oauthlib==1.5.0
librosa==1.0.0
mediapipe==1.0.1
numpy==2.5.3
opencv-contrib-python==5.0.0.93
pillow==12.3.0
pyyaml==6.0.3
scipy==1.18.1
```

`requirements-dev.txt`:

```text
pytest==9.1.1
ruff==0.16.10
```

`Dockerfile`:

```dockerfile
FROM python:3.12-slim AS base
LABEL org.opencontainers.image.source=https://github.com/rumstead/golf-etl
# libgl1 and libglib2.0 for opencv, libegl1 and libgles2 for mediapipe
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libegl1 libgles2 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ADD --chmod=644 https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task /opt/golf-etl/pose_landmarker_lite.task
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
RUN ruff check src tests && ruff format --check src tests && python -m pytest -q

FROM base
ARG GOLF_ETL_VERSION=dev
# matplotlib and fontconfig (pulled in by mediapipe) want a writable home for caches
ENV GOLF_ETL_VERSION=$GOLF_ETL_VERSION HOME=/tmp
RUN useradd --uid 10001 --no-create-home golf && mkdir /scratch && chown golf /scratch
USER 10001
ENTRYPOINT ["golf-etl"]
CMD ["poll-drive"]
```

`.dockerignore`:

```text
.git
.venv
out
local
**/__pycache__
.pytest_cache
.ruff_cache
build
*.egg-info
```

`.gitignore`:

```text
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
out/
local/
client_secret*.json
build/
```

`src/golf_etl/__init__.py` (empty file):

```python
```

`tests/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
import pytest

from golf_etl.config import Settings


def test_defaults_without_env():
    s = Settings.from_env({})
    assert s.onset_min_gap_s == 8.0
    assert s.root_folder == "golf"
    assert s.max_bytes == 3 * 1024**3


def test_env_overrides_are_cast_to_the_field_type():
    s = Settings.from_env(
        {"GOLF_ONSET_MIN_GAP_S": "6.5", "GOLF_CONFIRM_WINDOW_MS": "250", "GOLF_ROOT_FOLDER": "g2"}
    )
    assert s.onset_min_gap_s == 6.5
    assert s.confirm_window_ms == 250
    assert s.root_folder == "g2"


def test_unrelated_env_is_ignored():
    assert Settings.from_env({"GOLF_DRIVE_CLIENT_ID": "x", "HOME": "/root"}) == Settings()


def test_bad_value_names_the_variable():
    with pytest.raises(ValueError, match="GOLF_CLIP_TTL_DAYS"):
        Settings.from_env({"GOLF_CLIP_TTL_DAYS": "two weeks"})
```

- [ ] **Step 3: Build the test stage and watch it fail**

Run: `podman build --target test -t golf-etl:test .`
Expected: the build fails in the last `RUN` with `ModuleNotFoundError: No module named 'golf_etl.config'`. The first build takes several minutes (mediapipe, opencv, librosa); later ones reuse the cached layers.

- [ ] **Step 4: Write the implementation**

`src/golf_etl/config.py`:

```python
"""Runtime settings. Every field can be overridden with GOLF_<FIELD> in the environment."""

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # detection
    onset_highpass_hz: float = 2000.0
    onset_delta: float = 0.2
    onset_min_gap_s: float = 8.0
    impact_audio_lag_ms: float = 12.0
    pose_window_s: float = 1.5
    confirm_window_ms: int = 300
    confirm_min_speed: float = 1.0
    short_clip_s: float = 15.0
    pose_long_edge: int = 640
    pose_max_fps: float = 60.0
    pose_model_path: str = "/opt/golf-etl/pose_landmarker_lite.task"
    # slicing and frames
    pre_impact_s: float = 2.5
    post_impact_s: float = 1.5
    still_speed: float = 0.15
    sharpness_radius: int = 2
    frame_long_edge: int = 2560
    frame_max_pixels: int = 3_700_000  # Claude reads up to about 3.75MP
    jpeg_quality: int = 90
    clip_long_edge: int = 1920
    clip_crf: int = 23
    # drive
    root_folder: str = "golf"
    max_attempts: int = 3
    failed_ttl_days: int = 3
    clip_ttl_days: int = 14
    session_ttl_days: int = 60
    max_bytes: int = 3 * 1024**3
    scratch_dir: str = "/scratch"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        overrides = {}
        for field in dataclasses.fields(cls):
            key = f"GOLF_{field.name.upper()}"
            if key not in env:
                continue
            caster = type(field.default)
            try:
                overrides[field.name] = caster(env[key])
            except ValueError as exc:
                raise ValueError(f"{key}={env[key]!r} is not a valid {caster.__name__}") from exc
        return cls(**overrides)
```

- [ ] **Step 5: Build again and check the runtime image**

Run: `podman build --target test -t golf-etl:test . && podman build -t golf-etl . && podman run --rm golf-etl --help`
Expected: the test stage prints `4 passed`; `--help` fails with `ModuleNotFoundError: No module named 'golf_etl.cli'` (the CLI arrives in Task 10). That is expected here.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml requirements.txt requirements-dev.txt Dockerfile .dockerignore .gitignore src/golf_etl/__init__.py tests/__init__.py tests/test_config.py src/golf_etl/config.py
git commit -s -m "feat: scaffold the package, image, and settings"
```

### Task 3: Probe, decode, and encode video with ffmpeg

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_media.py`
- Create: `src/golf_etl/media.py`

**Interfaces:**
- Consumes: nothing.
- Produces in `golf_etl.media`:
  - `VideoInfo(duration_s: float, fps: float, width: int, height: int, audio_index: int | None, creation_time: datetime | None, hdr: bool = False)` with property `has_audio`. Width and height are after rotation.
  - `probe(path: Path) -> VideoInfo`; raises `RuntimeError("ffprobe failed: ...")` on unreadable files and `ValueError` when there is no video stream.
  - `pick_audio(streams: list[dict]) -> int | None`
  - `scaled_size(width, height, long_edge, even=False) -> tuple[int, int]` (never upscales)
  - `read_audio(path, info, sr) -> np.ndarray` (float32 mono, empty without audio)
  - `read_frames(path, info, start_s, end_s, long_edge=None, max_fps=None) -> Iterator[tuple[float, np.ndarray]]` (RGB uint8, SDR, clamped to the video)
  - `encode_clip(path, info, start_s, end_s, out, long_edge, crf) -> None`
- Produces in `tests/conftest.py`: `make_video(path, seconds=4.0, size="640x360", rate=30, rotate=None, audio=True, creation_time="2026-10-01T15:04:00Z")`, `make_video_with_clicks(path, clicks, seconds=10.0, size="1280x720", rate=30)`, fixture `video`.

Why the odd parts exist: iPhone `.mov` files carry an APAC spatial audio track first that ffmpeg cannot decode (so audio is mapped explicitly), are HDR HLG by default (so frames are tone mapped to BT.709), and are often portrait via a rotation matrix (so width and height swap).

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import subprocess
from pathlib import Path

import pytest


def make_video(
    path: Path,
    seconds: float = 4.0,
    size: str = "640x360",
    rate: int = 30,
    rotate: int | None = None,
    audio: bool = True,
    creation_time: str | None = "2026-10-01T15:04:00Z",
) -> Path:
    """Synthetic test video: moving test pattern plus a 440Hz tone."""
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate={rate}:duration={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    if rotate is not None:
        rotated = path.with_name(f"rot-{path.name}")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-display_rotation",
                str(rotate),
                "-i",
                str(path),
                "-c",
                "copy",
                str(rotated),
            ],
            check=True,
        )
        rotated.replace(path)
    return path


@pytest.fixture
def video(tmp_path: Path) -> Path:
    return make_video(tmp_path / "clip.mp4")


def make_video_with_clicks(
    path: Path, clicks: list[float], seconds: float = 10.0, size: str = "1280x720", rate: int = 30
) -> Path:
    """Test pattern video whose audio has impact-like clicks at the given times."""
    import numpy as np
    from scipy.io import wavfile

    sr = 22050
    rng = np.random.default_rng(0)
    y = rng.standard_normal(int(seconds * sr)) * 0.005
    n = int(0.02 * sr)
    for at in clicks:
        i = int(at * sr)
        y[i : i + n] += rng.standard_normal(n) * np.exp(-np.linspace(0, 8, n)) * 0.8
    wav = path.with_suffix(".wav")
    wavfile.write(wav, sr, (y * 32767).clip(-32768, 32767).astype(np.int16))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate={rate}:duration={seconds}",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path
```

`tests/test_media.py`:

```python
import subprocess
from datetime import UTC, datetime

import numpy as np
import pytest

from golf_etl import media
from tests.conftest import make_video

pytestmark = pytest.mark.media


def test_probe_reads_size_rate_audio_and_creation_time(video):
    info = media.probe(video)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(30.0)
    assert info.duration_s == pytest.approx(4.0, abs=0.1)
    assert info.has_audio
    assert info.creation_time == datetime(2026, 10, 1, 15, 4, tzinfo=UTC)


def test_probe_swaps_size_for_portrait_rotation(tmp_path):
    info = media.probe(make_video(tmp_path / "portrait.mp4", rotate=90))
    assert (info.width, info.height) == (360, 640)


def test_scaled_size_never_upscales():
    assert media.scaled_size(3840, 2160, 2560) == (2560, 1440)
    assert media.scaled_size(1280, 720, 2560) == (1280, 720)
    assert media.scaled_size(1081, 1921, 1920, even=True) == (1080, 1920)


def test_read_frames_window_count_and_shape(video):
    info = media.probe(video)
    frames = list(media.read_frames(video, info, 1.0, 2.0, long_edge=320))
    assert len(frames) == pytest.approx(30, abs=1)
    t0, img = frames[0]
    assert t0 == pytest.approx(1.0)
    assert img.shape == (180, 320, 3) and img.dtype == np.uint8


def test_read_frames_caps_fps(video):
    info = media.probe(video)
    frames = list(media.read_frames(video, info, 0.0, 1.0, long_edge=320, max_fps=10))
    assert len(frames) == pytest.approx(10, abs=1)


def test_read_frames_clamps_to_duration(video):
    info = media.probe(video)
    assert list(media.read_frames(video, info, 5.0, 6.0)) == []


def test_read_audio_is_mono_at_requested_rate(video):
    samples = media.read_audio(video, media.probe(video), 48000)
    assert samples.dtype == np.float32
    assert len(samples) == pytest.approx(4 * 48000, rel=0.02)


def test_encode_clip_is_cut_and_scaled(tmp_path, video):
    info = media.probe(video)
    out = tmp_path / "out.mp4"
    media.encode_clip(video, info, 1.0, 3.0, out, long_edge=320, crf=30)
    clip = media.probe(out)
    assert (clip.width, clip.height) == (320, 180)
    assert clip.duration_s == pytest.approx(2.0, abs=0.15)


def test_pick_audio_skips_the_undecodable_iphone_track():
    # stream layout of an iPhone 17 slo-mo .mov: APAC spatial audio first, then AAC
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        {"index": 1, "codec_type": "audio", "codec_tag_string": "apac"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac"},
        {"index": 3, "codec_type": "data", "codec_tag_string": "mebx"},
    ]
    assert media.pick_audio(streams) == 2
    assert media.pick_audio(streams[:2]) is None


def test_hdr_video_is_detected_and_tone_mapped(tmp_path):
    path = tmp_path / "hlg.mp4"
    x265 = "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=1"]
        + ["-vf", "format=yuv420p10le", "-c:v", "libx265", "-x265-params", x265, str(path)],
        check=True,
    )
    info = media.probe(path)
    assert info.hdr
    frames = list(media.read_frames(path, info, 0.0, 0.5))
    assert frames and frames[0][1].shape == (360, 640, 3)
    out = tmp_path / "clip.mp4"
    media.encode_clip(path, info, 0.0, 1.0, out, long_edge=320, crf=30)
    assert not media.probe(out).hdr
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_media.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.media`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/media.py`:

```python
"""ffmpeg and ffprobe wrappers. Frames are RGB uint8 arrays, rotated for display, SDR."""

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import numpy as np

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG (the iPhone default) and PQ
# Tone map HDR to SDR BT.709, otherwise 8-bit frames come out flat and washed out.
TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709"
)


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    fps: float
    width: int
    height: int
    audio_index: int | None  # first audio stream ffmpeg can decode
    creation_time: datetime | None
    hdr: bool = False

    @property
    def has_audio(self) -> bool:
        return self.audio_index is not None


def _run(cmd: list[str]) -> bytes:
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def pick_audio(streams: list[dict]) -> int | None:
    """iPhones put an APAC spatial audio track first that ffmpeg cannot decode; skip it."""
    for s in streams:
        if s["codec_type"] == "audio" and s.get("codec_name") not in (None, "none", "unknown"):
            return int(s["index"])
    return None


def probe(path: Path) -> VideoInfo:
    out = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
        + [str(path)]
    )
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ValueError(f"{path.name} has no video stream")
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []):
        rotation = int(side.get("rotation", rotation))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    tags = data["format"].get("tags", {})
    created = _parse_time(tags.get("com.apple.quicktime.creationdate")) or _parse_time(
        tags.get("creation_time")
    )
    return VideoInfo(
        duration_s=float(data["format"]["duration"]),
        fps=float(Fraction(video["avg_frame_rate"])),
        width=width,
        height=height,
        audio_index=pick_audio(data["streams"]),
        creation_time=created,
        hdr=video.get("color_transfer") in HDR_TRANSFERS,
    )


def scaled_size(width: int, height: int, long_edge: int, even: bool = False) -> tuple[int, int]:
    """Size that fits long_edge, never larger than the source."""
    scale = min(1.0, long_edge / max(width, height))
    w, h = round(width * scale), round(height * scale)
    if even:
        w, h = w - w % 2, h - h % 2
    return w, h


def _video_filters(info: VideoInfo, w: int, h: int, pix_fmt: str) -> list[str]:
    filters = [f"scale={w}:{h}"]
    if info.hdr:
        filters += [TONEMAP, f"format={pix_fmt}"]
    return filters


def read_audio(path: Path, info: VideoInfo, sr: int) -> np.ndarray:
    if info.audio_index is None:
        return np.zeros(0, dtype=np.float32)
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{info.audio_index}"]
    out = _run([*cmd, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"])
    return np.frombuffer(out, dtype=np.float32)


def read_frames(
    path: Path,
    info: VideoInfo,
    start_s: float,
    end_s: float,
    long_edge: int | None = None,
    max_fps: float | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp_s, rgb_frame) for frames in [start_s, end_s)."""
    start_s = max(0.0, start_s)
    end_s = min(info.duration_s, end_s)
    if end_s <= start_s:
        return
    if long_edge is None:
        w, h = info.width, info.height
    else:
        w, h = scaled_size(info.width, info.height, long_edge)
    fps = info.fps if not max_fps or info.fps <= max_fps else max_fps
    filters = _video_filters(info, w, h, "rgb24")
    if fps != info.fps:
        filters.insert(0, f"fps={fps}")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}"]
    cmd += ["-i", str(path), "-map", "0:v:0", "-vf", ",".join(filters)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    frame_bytes = w * h * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        assert proc.stdout is not None
        index = 0
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield start_s + index / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            index += 1
        proc.stdout.close()


def encode_clip(
    path: Path, info: VideoInfo, start_s: float, end_s: float, out: Path, long_edge: int, crf: int
) -> None:
    start_s = max(0.0, start_s)
    end_s = min(info.duration_s, end_s)
    w, h = scaled_size(info.width, info.height, long_edge, even=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}"]
    cmd += [
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        ",".join(_video_filters(info, w, h, "yuv420p")),
    ]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if info.audio_index is not None:
        cmd += ["-map", f"0:{info.audio_index}", "-c:a", "aac", "-b:a", "128k"]
    _run([*cmd, "-movflags", "+faststart", str(out)])
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_media.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `10 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_media.py src/golf_etl/media.py
git commit -s -m "feat: probe, decode, and encode video with ffmpeg"
```

### Task 4: Find impact candidates in the audio

**Files:**
- Test: `tests/test_audio.py`
- Create: `src/golf_etl/audio.py`

**Interfaces:**
- Consumes: nothing (pure numpy in, dataclasses out).
- Produces in `golf_etl.audio`: `SAMPLE_RATE = 48000`, `HOP = 128`, `Onset(time_s: float, strength: float)`, `Onsets(kept: list[Onset], suppressed: list[Onset])`, and `find_onsets(samples, sr, highpass_hz, delta, min_gap_s, lag_s=0.0) -> Onsets`. `kept` and `suppressed` are in time order; `strength` is 0..1 relative to the loudest onset.

The 48kHz rate, 128-sample hop, and backtracking came from measuring against the visible contact frames in `local/`: 22kHz with a 256 hop and no backtrack landed 32 to 34ms late, this setup lands 11 to 12ms late, and `lag_s` (12ms by default, from Settings) removes the rest.

- [ ] **Step 1: Write the failing tests**

`tests/test_audio.py`:

```python
import numpy as np
import pytest

from golf_etl.audio import find_onsets

SR = 48000


def track(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 0.005).astype(np.float32)


def add_click(y: np.ndarray, at_s: float, amp: float, seed: int = 1) -> None:
    """Broadband impact: 20ms of noise with a fast decay."""
    rng = np.random.default_rng(seed)
    n = int(0.02 * SR)
    burst = rng.standard_normal(n) * np.exp(-np.linspace(0, 8, n)) * amp
    i = int(at_s * SR)
    y[i : i + n] += burst.astype(np.float32)


def add_thump(y: np.ndarray, at_s: float, amp: float) -> None:
    """Low-frequency hit (a bag dropped, a footstep) that the high-pass should remove."""
    n = int(0.08 * SR)
    t = np.arange(n) / SR
    i = int(at_s * SR)
    y[i : i + n] += (np.sin(2 * np.pi * 120 * t) * np.exp(-t * 30) * amp).astype(np.float32)


def detect(y, **kw):
    args = {"highpass_hz": 2000.0, "delta": 0.2, "min_gap_s": 8.0, "lag_s": 0.0} | kw
    return find_onsets(y, SR, **args).kept


def test_finds_each_impact():
    y = track(30)
    for at in (5.0, 15.0, 25.0):
        add_click(y, at, 0.8)
    times = [o.time_s for o in detect(y)]
    assert times == pytest.approx([5.0, 15.0, 25.0], abs=0.03)


def test_weaker_onset_within_min_gap_is_dropped():
    y = track(30)
    add_click(y, 10.0, 0.8)
    add_click(y, 13.0, 0.3, seed=2)
    result = find_onsets(y, SR, highpass_hz=2000.0, delta=0.2, min_gap_s=8.0)
    assert [o.time_s for o in result.kept] == pytest.approx([10.0], abs=0.03)
    assert result.kept[0].strength == pytest.approx(1.0)
    assert [o.time_s for o in result.suppressed] == pytest.approx([13.0], abs=0.03)


def test_low_frequency_thump_is_ignored():
    y = track(20)
    add_click(y, 4.0, 0.8)
    add_thump(y, 14.0, 0.9)
    assert [o.time_s for o in detect(y)] == pytest.approx([4.0], abs=0.03)


def test_quieter_neighbor_far_away_is_kept_with_lower_strength():
    y = track(30)
    add_click(y, 5.0, 0.8)
    add_click(y, 20.0, 0.3, seed=3)
    onsets = detect(y)
    assert [o.time_s for o in onsets] == pytest.approx([5.0, 20.0], abs=0.03)
    assert onsets[1].strength < onsets[0].strength


def test_silence_and_tiny_input_return_nothing():
    assert detect(np.zeros(SR * 5, dtype=np.float32)) == []
    assert detect(np.zeros(10, dtype=np.float32)) == []


def test_times_start_at_the_transient_and_subtract_the_lag():
    y = track(10)
    add_click(y, 5.0, 0.8)
    assert detect(y)[0].time_s == pytest.approx(5.0, abs=0.01)  # within a 120fps frame
    shifted = detect(y, lag_s=0.012)[0].time_s
    assert shifted == pytest.approx(detect(y)[0].time_s - 0.012)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_audio.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.audio`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/audio.py`:

```python
"""Impact candidates from audio: sharp high-frequency transients."""

from dataclasses import dataclass

import librosa
import numpy as np
from scipy import signal

SAMPLE_RATE = 48000  # iPhone audio is 48kHz; downsampling smears the transient
HOP = 128


@dataclass(frozen=True)
class Onset:
    time_s: float  # start of the transient, minus the audio lag
    strength: float  # 0..1, relative to the strongest onset in the video


@dataclass(frozen=True)
class Onsets:
    kept: list[Onset]
    suppressed: list[Onset]  # within min_gap_s of a stronger onset


def find_onsets(
    samples: np.ndarray,
    sr: int,
    highpass_hz: float,
    delta: float,
    min_gap_s: float,
    lag_s: float = 0.0,
) -> Onsets:
    """Onsets in time order, keeping only the strongest within any min_gap_s span.

    Times are backtracked from the envelope peak to where the transient starts, then moved
    earlier by lag_s (sound travel to the phone plus the phone's own audio/video offset).
    """
    if samples.size < sr // 10:
        return Onsets([], [])
    sos = signal.butter(4, highpass_hz, btype="highpass", fs=sr, output="sos")
    filtered = signal.sosfiltfilt(sos, samples).astype(np.float32)
    env = librosa.onset.onset_strength(y=filtered, sr=sr, hop_length=HOP)
    peak = float(env.max())
    if peak <= 0:
        return Onsets([], [])
    env = env / peak
    peaks = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=HOP, units="frames", normalize=False, delta=delta
    )
    starts = librosa.onset.onset_backtrack(peaks, env)
    times = librosa.frames_to_time(starts, sr=sr, hop_length=HOP) - lag_s
    candidates = sorted(
        (Onset(max(0.0, float(t)), float(env[p])) for t, p in zip(times, peaks, strict=True)),
        key=lambda o: o.strength,
        reverse=True,
    )
    kept: list[Onset] = []
    suppressed: list[Onset] = []
    for onset in candidates:
        if all(abs(onset.time_s - k.time_s) >= min_gap_s for k in kept):
            kept.append(onset)
        else:
            suppressed.append(onset)
    return Onsets(sorted(kept, key=lambda o: o.time_s), sorted(suppressed, key=lambda o: o.time_s))
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_audio.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `6 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_audio.py src/golf_etl/audio.py
git commit -s -m "feat: find impact candidates in the audio"
```

### Task 5: Track the hands with MediaPipe pose

**Files:**
- Create: `tests/poses.py`
- Test: `tests/test_pose_track.py`
- Test: `tests/test_pose_estimator.py`
- Create: `src/golf_etl/pose.py`

**Interfaces:**
- Consumes: frames as `Iterable[tuple[float, np.ndarray]]` (Task 3 `read_frames`).
- Produces in `golf_etl.pose`: constants `LEFT_WRIST=15`, `RIGHT_WRIST=16`, `LEFT_ANKLE=27`, `RIGHT_ANKLE=28`, `NUM_LANDMARKS=33`; `PoseTrack(times: np.ndarray, landmarks: np.ndarray (n, 33, 3), aspect: float)` with `nearest(t) -> (33, 3)`, `hands() -> (n, 2)`, `hand_speed() -> (n,)` in frame heights per second, `hand_height() -> (n,)`; `PoseEstimator(model_path).track(frames) -> PoseTrack` (NaN rows when no person).
- Produces in `tests/poses.py`: `swing_track(impact_s, fps=60.0, before=2.5, after=1.5, missing=None)`, `still_track(center_s, fps=60.0, span=4.0)`, `empty_track(center_s, fps=60.0)`.

- [ ] **Step 1: Write the failing tests**

`tests/poses.py`:

```python
"""Synthetic pose tracks for tests: hands at address, up to the top, down through impact."""

import numpy as np

from golf_etl.pose import LEFT_ANKLE, LEFT_WRIST, NUM_LANDMARKS, RIGHT_ANKLE, RIGHT_WRIST, PoseTrack


def swing_track(
    impact_s: float,
    fps: float = 60.0,
    before: float = 2.5,
    after: float = 1.5,
    missing: tuple[float, float] | None = None,
) -> PoseTrack:
    """Still at address until impact-1.2s, top at impact-0.4s, fastest at impact, still by +0.8s."""
    times = np.arange(impact_s - before, impact_s + after, 1 / fps)
    rel = times - impact_s
    # hand height in normalized y (smaller is higher): 0.6 at address, 0.2 at the top
    y = np.full(times.shape, 0.6)
    back = (rel > -1.2) & (rel <= -0.4)
    y[back] = 0.6 - 0.4 * (rel[back] + 1.2) / 0.8
    down = (rel > -0.4) & (rel <= 0.0)
    y[down] = 0.2 + 0.4 * ((rel[down] + 0.4) / 0.4) ** 3  # accelerates into impact
    through = (rel > 0.0) & (rel <= 0.8)
    y[through] = 0.6 - 0.45 * np.sin(np.pi / 2 * rel[through] / 0.8)
    y[rel > 0.8] = 0.15
    lm = np.zeros((len(times), NUM_LANDMARKS, 3))
    lm[:, :, 2] = 0.9
    for idx in (LEFT_WRIST, RIGHT_WRIST):
        lm[:, idx, 0] = 0.5
        lm[:, idx, 1] = y
    lm[:, LEFT_ANKLE, :2] = (0.45, 0.9)
    lm[:, RIGHT_ANKLE, :2] = (0.55, 0.9)
    if missing:
        lm[(times >= missing[0]) & (times < missing[1])] = np.nan
    return PoseTrack(times, lm, aspect=16 / 9)


def still_track(center_s: float, fps: float = 60.0, span: float = 4.0) -> PoseTrack:
    """Someone standing still (the user waiting while a neighbor hits)."""
    times = np.arange(center_s - span / 2, center_s + span / 2, 1 / fps)
    lm = np.zeros((len(times), NUM_LANDMARKS, 3))
    lm[:, :, :2] = 0.5
    lm[:, :, 2] = 0.9
    return PoseTrack(times, lm, aspect=16 / 9)


def empty_track(center_s: float, fps: float = 60.0) -> PoseTrack:
    times = np.arange(center_s - 2, center_s + 2, 1 / fps)
    return PoseTrack(times, np.full((len(times), NUM_LANDMARKS, 3), np.nan), aspect=16 / 9)
```

`tests/test_pose_track.py`:

```python
import numpy as np
import pytest

from tests.poses import still_track, swing_track


def test_hand_speed_peaks_near_impact():
    track = swing_track(10.0)
    speed = track.hand_speed()
    assert track.times[int(np.nanargmax(speed))] == pytest.approx(10.0, abs=0.05)


def test_still_person_has_no_hand_speed():
    assert np.nanmax(still_track(5.0).hand_speed()) == pytest.approx(0.0)


def test_hand_height_highest_at_top():
    track = swing_track(10.0)
    before = track.times < 10.0
    top = track.times[before][int(np.argmax(track.hand_height()[before]))]
    assert top == pytest.approx(9.6, abs=0.05)


def test_nearest_returns_closest_sample():
    track = swing_track(10.0)
    lm = track.nearest(10.001)
    assert lm.shape == (33, 3)


def test_missing_pose_is_nan_speed():
    track = swing_track(10.0, missing=(8.0, 8.5))
    speed = track.hand_speed()
    gap = (track.times > 8.05) & (track.times < 8.45)
    assert np.all(np.isnan(speed[gap]))
```

`tests/test_pose_estimator.py`:

```python
import numpy as np
import pytest

from golf_etl.config import Settings
from golf_etl.pose import PoseEstimator

pytestmark = pytest.mark.media


def test_no_person_gives_nan_landmarks():
    frames = [(i / 30, np.full((360, 640, 3), 40, np.uint8)) for i in range(5)]
    track = PoseEstimator(Settings().pose_model_path).track(frames)
    assert track.landmarks.shape == (5, 33, 3)
    assert np.all(np.isnan(track.landmarks))
    assert track.aspect == pytest.approx(640 / 360)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_pose_track.py tests/test_pose_estimator.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.pose`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/pose.py`:

```python
"""Pose tracking with MediaPipe Pose Landmarker, reduced to what swing analysis needs."""

from collections.abc import Iterable
from dataclasses import dataclass

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_ANKLE, RIGHT_ANKLE = 27, 28
NUM_LANDMARKS = 33


@dataclass(frozen=True)
class PoseTrack:
    times: np.ndarray  # (n,) seconds
    landmarks: np.ndarray  # (n, 33, 3): x, y normalized to the frame, visibility; NaN when no pose
    aspect: float  # frame width / height

    def nearest(self, t: float) -> np.ndarray:
        """Landmarks (33, 3) of the sample closest to t."""
        return self.landmarks[int(np.argmin(np.abs(self.times - t)))]

    def hands(self) -> np.ndarray:
        """(n, 2) midpoint of both wrists, x scaled so both axes are in frame heights."""
        mid = (self.landmarks[:, LEFT_WRIST, :2] + self.landmarks[:, RIGHT_WRIST, :2]) / 2
        return mid * np.array([self.aspect, 1.0])

    def hand_speed(self) -> np.ndarray:
        """(n,) hand speed in frame heights per second, lightly smoothed; NaN without a pose."""
        if len(self.times) < 2:
            return np.full(len(self.times), np.nan)
        pos = self.hands()
        vel = np.gradient(pos, self.times, axis=0)
        speed = np.linalg.norm(vel, axis=1)
        kernel = np.ones(3) / 3
        padded = np.pad(speed, 1, mode="edge")
        return np.convolve(padded, kernel, mode="valid")

    def hand_height(self) -> np.ndarray:
        """(n,) hand height, larger is higher (image y grows downward)."""
        return -self.hands()[:, 1]


class PoseEstimator:
    def __init__(self, model_path: str):
        self.model_path = model_path

    def track(self, frames: Iterable[tuple[float, np.ndarray]]) -> PoseTrack:
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=2,
        )
        times: list[float] = []
        rows: list[np.ndarray] = []
        aspect = 1.0
        with vision.PoseLandmarker.create_from_options(options) as landmarker:
            for t, rgb in frames:
                aspect = rgb.shape[1] / rgb.shape[0]
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
                result = landmarker.detect_for_video(image, int(round(t * 1000)))
                times.append(t)
                rows.append(_largest_pose(result.pose_landmarks))
        landmarks = np.stack(rows) if rows else np.empty((0, NUM_LANDMARKS, 3))
        return PoseTrack(np.asarray(times), landmarks, aspect)


def _largest_pose(poses) -> np.ndarray:
    """The golfer is the biggest person in frame; neighbors in other bays are smaller."""
    if not poses:
        return np.full((NUM_LANDMARKS, 3), np.nan)
    arrays = [np.array([[p.x, p.y, p.visibility or 0.0] for p in pose]) for pose in poses]

    def area(a: np.ndarray) -> float:
        return float(np.ptp(a[:, 0]) * np.ptp(a[:, 1]))

    return max(arrays, key=area)
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_pose_track.py tests/test_pose_estimator.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `6 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/poses.py tests/test_pose_track.py tests/test_pose_estimator.py src/golf_etl/pose.py
git commit -s -m "feat: track hands with mediapipe pose"
```

### Task 6: Confirm swings with a hand speed peak

**Files:**
- Test: `tests/test_detect.py`
- Create: `src/golf_etl/detect.py`

**Interfaces:**
- Consumes: `Onset` (Task 4), `PoseTrack` (Task 5), `Settings` (Task 2).
- Produces in `golf_etl.detect`: `Swing(impact_s, strength, peak_offset_ms, track)`, `Rejected(time_s, strength, reason)`, `Detection(swings: list[Swing], rejected: list[Rejected])`, `confirm(track, onset_s, cfg) -> tuple[float | None, str]`, and `detect_swings(onsets, track_for: Callable[[float], PoseTrack], duration_s, cfg, suppressed=None) -> Detection`. Rejection reasons are user-facing strings that land in `session.md`.

- [ ] **Step 1: Write the failing tests**

`tests/test_detect.py`:

```python
import pytest

from golf_etl.audio import Onset
from golf_etl.config import Settings
from golf_etl.detect import confirm, detect_swings
from tests.poses import empty_track, still_track, swing_track

CFG = Settings()


def test_confirms_swing_with_peak_at_the_sound():
    offset, reason = confirm(swing_track(10.0), 10.0, CFG)
    assert reason == ""
    assert abs(offset) <= CFG.confirm_window_ms


def test_rejects_when_peak_is_far_from_the_sound():
    # neighbor's impact 1s before the user's own downswing
    offset, reason = confirm(swing_track(11.0), 10.0, CFG)
    assert offset is None
    assert "from the sound" in reason


def test_rejects_still_person_and_no_pose():
    assert confirm(still_track(10.0), 10.0, CFG)[1].startswith("hands too slow")
    assert confirm(empty_track(10.0), 10.0, CFG) == (None, "no pose found")


def test_detect_splits_swings_and_rejections_in_time_order():
    tracks = {5.0: swing_track(5.0), 14.0: still_track(14.0), 25.0: swing_track(25.0)}
    onsets = [Onset(5.0, 1.0), Onset(14.0, 0.4), Onset(25.0, 0.9)]
    result = detect_swings(onsets, tracks.__getitem__, duration_s=600, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [5.0, 25.0]
    assert [r.time_s for r in result.rejected] == [14.0]


def test_short_clip_keeps_only_strongest_swing():
    tracks = {2.6: swing_track(2.6), 9.0: swing_track(9.0)}
    onsets = [Onset(2.6, 0.7), Onset(9.0, 1.0)]
    result = detect_swings(onsets, tracks.__getitem__, duration_s=12, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [9.0]
    assert result.rejected[0].reason == "single-swing clip keeps the strongest"


def test_no_onsets_is_empty_not_an_error():
    result = detect_swings([], lambda t: pytest.fail("no tracks needed"), 30, CFG)
    assert result.swings == [] and result.rejected == []


def test_loud_onset_dropped_by_the_gap_is_reported():
    loud, quiet = Onset(12.0, 0.9), Onset(13.0, 0.2)
    result = detect_swings(
        [Onset(5.0, 1.0)], lambda t: swing_track(t), 600, CFG, suppressed=[loud, quiet]
    )
    assert [(r.time_s, r.reason) for r in result.rejected] == [
        (12.0, "within 8s of a stronger sound")
    ]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_detect.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.detect`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/detect.py`:

```python
"""Swing detection: audio onsets confirmed by a hand speed peak in the pose track."""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from golf_etl.audio import Onset
from golf_etl.config import Settings
from golf_etl.pose import PoseTrack

# Suppressed onsets quieter than this are background noise and stay out of session.md.
REPORT_SUPPRESSED_ABOVE = 0.5


@dataclass(frozen=True)
class Swing:
    impact_s: float
    strength: float
    peak_offset_ms: float  # hand speed peak minus the audio onset
    track: PoseTrack


@dataclass(frozen=True)
class Rejected:
    time_s: float
    strength: float
    reason: str


@dataclass(frozen=True)
class Detection:
    swings: list[Swing]
    rejected: list[Rejected]


def confirm(track: PoseTrack, onset_s: float, cfg: Settings) -> tuple[float | None, str]:
    """(peak offset in ms, "") when the swing is confirmed, else (None, reason)."""
    near = np.abs(track.times - onset_s) <= cfg.pose_window_s
    speed = track.hand_speed()[near]
    if speed.size == 0 or np.all(np.isnan(speed)):
        return None, "no pose found"
    i = int(np.nanargmax(speed))
    peak_speed = float(speed[i])
    offset_ms = (float(track.times[near][i]) - onset_s) * 1000
    if peak_speed < cfg.confirm_min_speed:
        return None, f"hands too slow ({peak_speed:.2f} frame heights/s)"
    if abs(offset_ms) > cfg.confirm_window_ms:
        return None, f"hand speed peak {offset_ms:+.0f}ms from the sound"
    return offset_ms, ""


def detect_swings(
    onsets: list[Onset],
    track_for: Callable[[float], PoseTrack],
    duration_s: float,
    cfg: Settings,
    suppressed: list[Onset] | None = None,
) -> Detection:
    swings: list[Swing] = []
    rejected: list[Rejected] = []
    for onset in onsets:
        track = track_for(onset.time_s)
        offset_ms, reason = confirm(track, onset.time_s, cfg)
        if offset_ms is None:
            rejected.append(Rejected(onset.time_s, onset.strength, reason))
        else:
            swings.append(Swing(onset.time_s, onset.strength, offset_ms, track))
    if duration_s < cfg.short_clip_s and len(swings) > 1:
        best = max(swings, key=lambda s: s.strength)
        rejected += [
            Rejected(s.impact_s, s.strength, "single-swing clip keeps the strongest")
            for s in swings
            if s is not best
        ]
        swings = [best]
    rejected += [
        Rejected(o.time_s, o.strength, f"within {cfg.onset_min_gap_s:g}s of a stronger sound")
        for o in suppressed or []
        if o.strength >= REPORT_SUPPRESSED_ABOVE
    ]
    rejected.sort(key=lambda r: r.time_s)
    return Detection(swings, rejected)
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_detect.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `7 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_detect.py src/golf_etl/detect.py
git commit -s -m "feat: confirm swings with a hand speed peak"
```

### Task 7: Pick address, top, impact, and finish

**Files:**
- Test: `tests/test_frames.py`
- Create: `src/golf_etl/frames.py`

**Interfaces:**
- Consumes: `PoseTrack` (Task 5).
- Produces in `golf_etl.frames`: `Positions(address_s, top_s, impact_s, finish_s)` with `items() -> list[tuple[str, float]]` (the four key positions, in order) and `sequence() -> list[tuple[str, float]]` (eight positions: address, takeaway, halfway back, top, transition, impact, follow-through, finish), `pick_positions(track, impact_s, still_speed) -> Positions`, `sharpness(rgb) -> float`, `sharpest(frames) -> tuple[float, np.ndarray]`.

Finish is the highest hands after impact, not the first still moment: on the real clips the first still moment caught a pause in the follow-through. The four in-between sequence positions are spaced in time (thirds of address to top, halves of top to impact and impact to finish), which put transition at hands-at-shoulder on the way down on the real clips.

- [ ] **Step 1: Write the failing tests**

`tests/test_frames.py`:

```python
import cv2
import numpy as np
import pytest

from golf_etl.frames import pick_positions, sharpest
from tests.poses import swing_track


def test_positions_on_a_clean_swing():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.top_s == pytest.approx(9.6, abs=0.05)
    assert 8.0 <= p.address_s <= 8.85  # still before takeaway at 8.8
    assert p.impact_s == 10.0
    assert p.finish_s == pytest.approx(10.8, abs=0.05)  # hands highest after impact


def test_positions_without_pose_fall_back_to_offsets():
    track = swing_track(10.0, missing=(7.0, 12.0))
    p = pick_positions(track, 10.0, still_speed=0.15)
    assert (p.address_s, p.top_s, p.finish_s) == pytest.approx((8.2, 9.2, track.times[-1]))


def test_order_is_address_top_impact_finish():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.address_s < p.top_s < p.impact_s < p.finish_s


def test_sharpest_prefers_the_unblurred_frame():
    rng = np.random.default_rng(0)
    crisp = (rng.random((120, 160, 3)) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(crisp, (9, 9), 4)
    t, img = sharpest([(1.0, blurred), (1.1, crisp), (1.2, blurred)])
    assert t == 1.1 and img is crisp


def test_sequence_has_eight_positions_in_time_order():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    seq = p.sequence()
    assert [name for name, _ in seq] == [
        "address",
        "takeaway",
        "halfway back",
        "top",
        "transition",
        "impact",
        "follow-through",
        "finish",
    ]
    times = [t for _, t in seq]
    assert times == sorted(times)
    assert dict(seq)["transition"] == pytest.approx((p.top_s + p.impact_s) / 2)
    assert dict(seq)["takeaway"] == pytest.approx(p.address_s + (p.top_s - p.address_s) / 3)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_frames.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.frames`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/frames.py`:

```python
"""Key swing positions from the pose track, and picking the sharpest nearby frame."""

from dataclasses import dataclass

import cv2
import numpy as np

from golf_etl.pose import PoseTrack

STILL_MIN_S = 0.2
FINISH_AFTER_S = 0.3


@dataclass(frozen=True)
class Positions:
    address_s: float
    top_s: float
    impact_s: float
    finish_s: float

    def items(self) -> list[tuple[str, float]]:
        """The four key positions, saved as full frames."""
        return [
            ("address", self.address_s),
            ("top", self.top_s),
            ("impact", self.impact_s),
            ("finish", self.finish_s),
        ]

    def sequence(self) -> list[tuple[str, float]]:
        """Eight positions for the sequence sheet; the in-between ones are spaced in time."""
        back = self.top_s - self.address_s
        return [
            ("address", self.address_s),
            ("takeaway", self.address_s + back / 3),
            ("halfway back", self.address_s + 2 * back / 3),
            ("top", self.top_s),
            ("transition", (self.top_s + self.impact_s) / 2),
            ("impact", self.impact_s),
            ("follow-through", (self.impact_s + self.finish_s) / 2),
            ("finish", self.finish_s),
        ]


def pick_positions(track: PoseTrack, impact_s: float, still_speed: float) -> Positions:
    times = track.times
    speed = track.hand_speed()
    height = track.hand_height()
    start, end = float(times[0]), float(times[-1])

    before = (times < impact_s) & (times >= impact_s - 2.0) & ~np.isnan(height)
    if before.any():
        top_s = float(times[before][int(np.argmax(height[before]))])
    else:
        top_s = max(start, impact_s - 0.8)

    address_s = _last_still_before(times, speed, top_s, still_speed)
    if address_s is None:
        address_s = max(start, top_s - 1.0)

    after = (times >= impact_s + FINISH_AFTER_S) & ~np.isnan(height)
    finish_s = float(times[after][int(np.argmax(height[after]))]) if after.any() else end
    return Positions(address_s, top_s, impact_s, finish_s)


def _last_still_before(
    times: np.ndarray, speed: np.ndarray, before_s: float, still_speed: float
) -> float | None:
    """End of the last run of still samples lasting STILL_MIN_S, before before_s."""
    still = (speed < still_speed) & (times < before_s)
    run_start = None
    best = None
    for t, is_still in zip(times, still, strict=True):
        if is_still:
            run_start = t if run_start is None else run_start
            if t - run_start >= STILL_MIN_S:
                best = float(t)
        else:
            run_start = None
    return best


def sharpness(rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpest(frames: list[tuple[float, np.ndarray]]) -> tuple[float, np.ndarray]:
    return max(frames, key=lambda f: sharpness(f[1]))
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_frames.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `5 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_frames.py src/golf_etl/frames.py
git commit -s -m "feat: pick address, top, impact, and finish"
```

### Task 8: Render labeled frames, the impact zoom band, and the sequence sheet

**Files:**
- Test: `tests/test_render.py`
- Create: `src/golf_etl/render.py`

**Interfaces:**
- Consumes: landmark arrays from `PoseTrack.nearest` (Task 5).
- Produces in `golf_etl.render`: `fit(rgb, long_edge, max_pixels=None)`, `label(rgb, text)`, `draw_pose(rgb, landmarks)`, `grid(images, labels, cols, long_edge, max_pixels)`, `zoom_crop(rgb, landmarks)`, `save_jpeg(rgb, path, quality)`. All take and return RGB uint8 arrays; none modify their input.

The zoom is a band at ankle height across the frame because the ball is between the feet face-on but well past the toes down the line; an ankle-centered square missed the ball entirely on the real clips. `grid` sizes the sheet to both the long edge and the pixel budget (eight portrait panels would otherwise be 5.8MP) and labels each panel after resizing so the text stays legible.

- [ ] **Step 1: Write the failing tests**

`tests/test_render.py`:

```python
import numpy as np
import pytest
from PIL import Image

from golf_etl import render
from tests.poses import swing_track


def frame(h=2160, w=3840, value=90):
    return np.full((h, w, 3), value, np.uint8)


def test_fit_downscales_to_long_edge_and_never_upscales():
    assert render.fit(frame(), 2560).shape == (1440, 2560, 3)
    small = frame(720, 1280)
    assert render.fit(small, 2560) is small


def test_fit_respects_the_pixel_budget():
    h, w = render.fit(frame(1620, 2592), 2560, max_pixels=3_700_000).shape[:2]
    assert max(h, w) <= 2560 and h * w <= 3_700_000


def test_label_draws_without_touching_the_input():
    src = frame(720, 1280)
    out = render.label(src, "address 12.34s")
    assert (src == 90).all()
    assert (out[:4, :4] == 0).all()  # black label box padding in the corner
    assert (out != 90).sum() > 1000


def test_draw_pose_marks_pixels_and_skips_missing_pose():
    lm = swing_track(10.0).nearest(10.0)
    lm[[11, 12, 13, 14, 23, 24, 25, 26], :2] = 0.5
    assert (render.draw_pose(frame(720, 1280), lm) != 90).any()
    empty = np.full((33, 3), np.nan)
    assert (render.draw_pose(frame(720, 1280), empty) == 90).all()


def test_grid_of_eight_portrait_frames_fits_claude():
    sheet = render.grid([frame(1920, 1080)] * 8, [f"p{i}" for i in range(8)], 4, 2560, 3_700_000)
    h, w = sheet.shape[:2]
    assert max(h, w) <= 2560 and h * w <= 3_700_000
    assert w / h == pytest.approx((4 * 1080) / (2 * 1920), rel=0.01)  # two rows of four


def test_grid_of_eight_landscape_frames_fits_the_long_edge():
    sheet = render.grid([frame(720, 1280)] * 8, ["x"] * 8, 4, 2560, 3_700_000)
    assert sheet.shape == (720, 2560, 3)


def test_grid_pads_a_short_last_row():
    sheet = render.grid([frame(720, 1280)] * 3, ["a", "b", "c"], 2, 2560, 3_700_000)
    h, w = sheet.shape[:2]
    assert (sheet[h // 2 :, w // 2 :] == 0).all()


def test_zoom_crop_is_a_native_band_at_ankle_height():
    lm = swing_track(10.0).nearest(10.0)  # ankles at x 0.45..0.55, y 0.9
    landscape = render.zoom_crop(frame(), lm)
    assert landscape.shape == (540, 2592, 3)  # 0.25 x 2160 tall, 1.2 x 2160 wide
    portrait = render.zoom_crop(frame(1920, 1080), lm)
    assert portrait.shape == (480, 1080, 3)  # full width when the frame is narrow


def test_zoom_crop_stays_inside_the_frame_near_the_edge():
    lm = swing_track(10.0).nearest(10.0)
    lm[[27, 28], :2] = (0.99, 0.99)
    assert render.zoom_crop(frame(), lm).shape == (540, 2592, 3)


def test_save_jpeg_is_rgb_without_exif(tmp_path):
    path = tmp_path / "f.jpg"
    render.save_jpeg(frame(100, 200), path, 90)
    with Image.open(path) as img:
        assert img.mode == "RGB"
        assert img.format == "JPEG"
        assert not img.getexif()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_render.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.render`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/render.py`:

```python
"""Turning RGB frames into the labeled JPEGs Claude reads."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from golf_etl.pose import LEFT_ANKLE, RIGHT_ANKLE

# Shoulders, arms, torso, legs. Face and hand landmarks only add clutter.
SKELETON = [
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (24, 26),
    (26, 28),
    (27, 31),
    (28, 32),
]
ZOOM_BAND = 0.25  # impact zoom band height as a fraction of frame height


def fit(rgb: np.ndarray, long_edge: int, max_pixels: int | None = None) -> np.ndarray:
    """Downscale to fit long_edge (and max_pixels when given). Never upscales."""
    h, w = rgb.shape[:2]
    scale = long_edge / max(h, w)
    if max_pixels:
        scale = min(scale, (max_pixels / (w * h)) ** 0.5)
    if scale >= 1:
        return rgb
    size = (int(w * scale), int(h * scale))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)


def label(rgb: np.ndarray, text: str) -> np.ndarray:
    out = rgb.copy()
    h, w = out.shape[:2]
    scale = max(0.5, w / 1600)
    thickness = max(1, round(scale * 2))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    pad = round(10 * scale)
    cv2.rectangle(out, (0, 0), (tw + 2 * pad, th + base + 2 * pad), (0, 0, 0), -1)
    cv2.putText(
        out,
        text,
        (pad, pad + th),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return out


def draw_pose(rgb: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    if np.isnan(landmarks).all():
        return out
    h, w = out.shape[:2]
    pts = {i: (int(x * w), int(y * h)) for i, (x, y, _) in enumerate(landmarks) if not np.isnan(x)}
    thickness = max(2, w // 400)
    for a, b in SKELETON:
        if a in pts and b in pts:
            cv2.line(out, pts[a], pts[b], (0, 255, 0), thickness, cv2.LINE_AA)
    for i in {i for pair in SKELETON for i in pair} & pts.keys():
        cv2.circle(out, pts[i], thickness * 2, (255, 64, 64), -1, cv2.LINE_AA)
    return out


def grid(
    images: list[np.ndarray], labels: list[str], cols: int, long_edge: int, max_pixels: int
) -> np.ndarray:
    """Sheet in reading order, sized to fit long_edge and max_pixels, labeled after resizing."""
    h, w = images[0].shape[:2]
    rows = -(-len(images) // cols)
    scale = min(long_edge / max(cols * w, rows * h), (max_pixels / (cols * w * rows * h)) ** 0.5)
    cell_w, cell_h = int(w * scale), int(h * scale)
    cells = [
        label(cv2.resize(img, (cell_w, cell_h), interpolation=cv2.INTER_AREA), text)
        for img, text in zip(images, labels, strict=True)
    ]
    cells += [np.zeros((cell_h, cell_w, 3), np.uint8)] * (rows * cols - len(cells))
    return np.vstack([np.hstack(cells[r * cols : (r + 1) * cols]) for r in range(rows)])


def zoom_crop(rgb: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    """Native-resolution band at ball height (ankle level).

    The ball sits between the feet face-on but out past the toes down the line, so the band
    spans the frame width (up to 1.2 frame heights) instead of guessing where the ball is.
    """
    h, w = rgb.shape[:2]
    band_h = round(h * ZOOM_BAND)
    band_w = min(w, round(h * 1.2))
    ankles = landmarks[[LEFT_ANKLE, RIGHT_ANKLE], :2]
    if np.isnan(ankles).any():
        cx, cy = w / 2, h * 0.8
    else:
        cx, cy = ankles[:, 0].mean() * w, ankles[:, 1].mean() * h
    x0 = int(np.clip(cx - band_w / 2, 0, w - band_w))
    y0 = int(np.clip(cy - band_h / 2, 0, h - band_h))
    return rgb[y0 : y0 + band_h, x0 : x0 + band_w]


def save_jpeg(rgb: np.ndarray, path: Path, quality: int) -> None:
    """sRGB JPEG with no EXIF or other metadata."""
    Image.fromarray(rgb).save(path, "JPEG", quality=quality, optimize=True)
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_render.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `10 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_render.py src/golf_etl/render.py
git commit -s -m "feat: render labeled frames, zoom band, and sequence sheet"
```

### Task 9: Process a video into a session folder

**Files:**
- Test: `tests/test_session.py`
- Test: `tests/test_pipeline.py`
- Create: `src/golf_etl/session.py`
- Create: `src/golf_etl/pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 8.
- Produces in `golf_etl.session`: `session_id(captured: datetime, checksum: str) -> str` (`YYYY-MM-DD-HHMM-<8 hex>`), `GUIDE` (the how-to-read section, written when there are swings), `SessionSummary`, `write_session_md(path, summary)`.
- Produces in `golf_etl.pipeline`: `SessionResult(session_id, session_dir, swings, rejected)`, `Detector = Callable[[Path, VideoInfo, Settings], Detection]`, `detect_video(video, info, cfg) -> Detection`, `process_video(video, out_root, cfg, *, source_name, checksum, uploaded_at, duplicates=None, version="dev", detect=detect_video) -> SessionResult`, `write_swing(video, info, swing, out, cfg)` (writes `01`-`04` key frames, `05-impact-zoom.jpg`, `06-sequence.jpg`, `clip.mp4`). `process_video` raises on unreadable input; the poller relies on that.

- [ ] **Step 1: Write the failing tests**

`tests/test_session.py`:

```python
from datetime import UTC, datetime

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


def test_session_md_with_no_swings_says_so(tmp_path):
    path = tmp_path / "session.md"
    write_session_md(path, summary())
    assert "No swings detected." in path.read_text()
    assert "Rejected" not in path.read_text()
    assert "How to read" not in path.read_text()
```

`tests/test_pipeline.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_session.py tests/test_pipeline.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.session`, `golf_etl.pipeline`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/session.py`:

```python
"""Session naming and the session.md summary."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from golf_etl.detect import Rejected, Swing

GUIDE = """## How to read this session

- Each `swing-NN/` folder is one swing, in the order hit.
- Start with `06-sequence.jpg`: the whole swing in one image, read left to right, top row \
then bottom row: address, takeaway, halfway back, top, transition, impact, follow-through, finish.
- `05-impact-zoom.jpg` is the ball area at full resolution for the frames just before, at, \
and after impact, top to bottom. Use it for contact, shaft lean, and strike.
- `01-address.jpg` to `04-finish.jpg` are the key positions at full resolution for a closer look.
- `clip.mp4` is the swing as video.
- Every image has its position and its time in the video in the top-left corner.
- The skeleton lines on the sequence sheet are a pose estimate; trust the photo over the lines.
"""


def session_id(captured: datetime, checksum: str) -> str:
    return f"{captured:%Y-%m-%d-%H%M}-{checksum[:8]}"


@dataclass
class SessionSummary:
    session_id: str
    source_name: str
    captured: datetime
    checksum: str
    version: str
    swings: list[Swing]
    rejected: list[Rejected]
    duplicates: list[str] = field(default_factory=list)


def write_session_md(path: Path, s: SessionSummary) -> None:
    lines = [
        f"# Session {s.session_id}",
        "",
        f"- Source: {s.source_name}",
        f"- Captured: {s.captured:%Y-%m-%d %H:%M %Z}".rstrip(),
        f"- Checksum: {s.checksum}",
        f"- Pipeline: {s.version}",
        f"- Swings: {len(s.swings)}",
        "",
    ]
    if s.swings:
        lines += [GUIDE]
        lines += ["| Swing | Impact | Strength | Hand speed peak |", "|---|---|---|---|"]
        for n, swing in enumerate(s.swings, 1):
            lines.append(
                f"| swing-{n:02d} | {swing.impact_s:.2f}s | {swing.strength:.2f} "
                f"| {swing.peak_offset_ms:+.0f}ms |"
            )
    else:
        lines.append("No swings detected.")
    if s.rejected:
        lines += ["", "## Rejected candidates", "", "| Time | Strength | Reason |", "|---|---|---|"]
        lines += [f"| {r.time_s:.2f}s | {r.strength:.2f} | {r.reason} |" for r in s.rejected]
    if s.duplicates:
        lines += ["", "## Removed duplicates", ""] + [f"- {name}" for name in s.duplicates]
    path.write_text("\n".join(lines) + "\n")
```

`src/golf_etl/pipeline.py`:

```python
"""One video in, one session folder out."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from golf_etl import render
from golf_etl.audio import SAMPLE_RATE, Onsets, find_onsets
from golf_etl.config import Settings
from golf_etl.detect import Detection, Rejected, Swing, detect_swings
from golf_etl.frames import pick_positions, sharpest
from golf_etl.media import VideoInfo, encode_clip, probe, read_audio, read_frames
from golf_etl.pose import PoseEstimator
from golf_etl.session import SessionSummary, session_id, write_session_md

Detector = Callable[[Path, VideoInfo, Settings], Detection]


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    session_dir: Path
    swings: int
    rejected: list[Rejected]


def detect_video(video: Path, info: VideoInfo, cfg: Settings) -> Detection:
    onsets = Onsets([], [])
    if info.has_audio:
        samples = read_audio(video, info, SAMPLE_RATE)
        onsets = find_onsets(
            samples,
            SAMPLE_RATE,
            cfg.onset_highpass_hz,
            cfg.onset_delta,
            cfg.onset_min_gap_s,
            cfg.impact_audio_lag_ms / 1000,
        )
    estimator = PoseEstimator(cfg.pose_model_path)

    def track_for(t: float):
        frames = read_frames(
            video,
            info,
            t - cfg.pre_impact_s,
            t + cfg.post_impact_s,
            cfg.pose_long_edge,
            cfg.pose_max_fps,
        )
        return estimator.track(frames)

    return detect_swings(onsets.kept, track_for, info.duration_s, cfg, onsets.suppressed)


def process_video(
    video: Path,
    out_root: Path,
    cfg: Settings,
    *,
    source_name: str,
    checksum: str,
    uploaded_at: datetime,
    duplicates: list[str] | None = None,
    version: str = "dev",
    detect: Detector = detect_video,
) -> SessionResult:
    info = probe(video)
    detection = detect(video, info, cfg)
    sid = session_id(info.creation_time or uploaded_at, checksum)
    session_dir = out_root / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    for n, swing in enumerate(detection.swings, 1):
        write_swing(video, info, swing, session_dir / f"swing-{n:02d}", cfg)
    write_session_md(
        session_dir / "session.md",
        SessionSummary(
            session_id=sid,
            source_name=source_name,
            captured=info.creation_time or uploaded_at,
            checksum=checksum,
            version=version,
            swings=detection.swings,
            rejected=detection.rejected,
            duplicates=duplicates or [],
        ),
    )
    return SessionResult(sid, session_dir, len(detection.swings), detection.rejected)


def write_swing(video: Path, info: VideoInfo, swing: Swing, out: Path, cfg: Settings) -> None:
    out.mkdir(parents=True, exist_ok=True)
    positions = pick_positions(swing.track, swing.impact_s, cfg.still_speed)
    dt = 1 / info.fps
    picked: dict[str, tuple[float, np.ndarray]] = {}
    impact_frames: list[np.ndarray] = []
    for name, t in positions.sequence():
        if name == "impact":
            window = list(read_frames(video, info, t - 1.5 * dt, t + 1.5 * dt))
            if not window:
                raise RuntimeError(f"no frames decoded around impact at {t:.2f}s")
            i = int(np.argmin([abs(ft - t) for ft, _ in window]))
            impact_frames = [img for _, img in window[max(0, i - 1) : i + 2]]
            picked[name] = window[i]
        else:
            r = cfg.sharpness_radius + 0.5
            window = list(read_frames(video, info, t - r * dt, t + r * dt))
            if not window:
                raise RuntimeError(f"no frames decoded around {name} at {t:.2f}s")
            picked[name] = sharpest(window)

    for n, (name, _) in enumerate(positions.items(), 1):
        ft, img = picked[name]
        frame = render.fit(img, cfg.frame_long_edge, cfg.frame_max_pixels)
        render.save_jpeg(
            render.label(frame, f"{name} {ft:.2f}s"), out / f"{n:02d}-{name}.jpg", cfg.jpeg_quality
        )

    address_pose = swing.track.nearest(positions.address_s)
    crops = [render.zoom_crop(img, address_pose) for img in impact_frames]
    names = ["impact -1", "impact", "impact +1"] if len(crops) == 3 else ["impact"] * len(crops)
    strip = np.vstack([render.label(c, n) for c, n in zip(crops, names, strict=True)])
    zoom = render.fit(strip, cfg.frame_long_edge, cfg.frame_max_pixels)
    render.save_jpeg(zoom, out / "05-impact-zoom.jpg", cfg.jpeg_quality)

    overlays, labels = [], []
    for name, _ in positions.sequence():
        ft, img = picked[name]
        small = render.fit(img, cfg.frame_long_edge // 2)
        overlays.append(render.draw_pose(small, swing.track.nearest(ft)))
        labels.append(f"{name} {ft:.2f}s")
    sheet = render.grid(overlays, labels, 4, cfg.frame_long_edge, cfg.frame_max_pixels)
    render.save_jpeg(sheet, out / "06-sequence.jpg", cfg.jpeg_quality)

    encode_clip(
        video,
        info,
        swing.impact_s - cfg.pre_impact_s,
        swing.impact_s + cfg.post_impact_s,
        out / "clip.mp4",
        cfg.clip_long_edge,
        cfg.clip_crf,
    )
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_session.py tests/test_pipeline.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `8 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_session.py tests/test_pipeline.py src/golf_etl/session.py src/golf_etl/pipeline.py
git commit -s -m "feat: process a video into a session folder"
```

### Task 10: Add the process and eval commands

**Files:**
- Test: `tests/test_eval.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_real.py`
- Create: `src/golf_etl/eval.py`
- Create: `src/golf_etl/cli.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `process_video`, `detect_video` (Task 9).
- Produces in `golf_etl.eval`: `Score` (precision, recall, mean_error_ms, `line(name)`), `score(predicted, truth, tolerance_s) -> Score`, `run(labels_path, cfg, detect_times) -> tuple[Score, list[str]]`, `detected_impacts(video, cfg) -> list[float]`.
- Produces in `golf_etl.cli`: `main(argv=None) -> int` with `process`, `eval`, `poll-drive`, `auth`. `poll-drive` and `auth` import the Drive modules lazily, so they only work after Task 13; nothing here calls them.

`tests/test_cli.py` here is the version without the poll-drive test; Task 13 appends it. `local/` already holds `IMG_4439-a.mov`, `IMG_4439-b.mov`, and `labels.yaml` from planning; `tests/test_real.py` skips without them.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval.py`:

```python
import pytest

from golf_etl.config import Settings
from golf_etl.eval import run, score


def test_perfect_detection():
    s = score([5.01, 15.0], [5.0, 15.0], tolerance_s=0.15)
    assert (s.true_pos, s.false_pos, s.false_neg) == (2, 0, 0)
    assert s.mean_error_ms == pytest.approx(5.0)


def test_misses_and_extras():
    s = score([5.0, 9.0, 30.0], [5.0, 15.0], tolerance_s=0.15)
    assert (s.true_pos, s.false_pos, s.false_neg) == (1, 2, 1)
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(0.5)


def test_one_prediction_cannot_match_two_impacts():
    s = score([10.0], [9.95, 10.05], tolerance_s=0.15)
    assert (s.true_pos, s.false_neg) == (1, 1)


def test_empty_inputs_are_perfect():
    s = score([], [], 0.15)
    assert s.precision == 1.0 and s.recall == 1.0


def test_run_reads_labels_relative_to_the_file(tmp_path):
    (tmp_path / "labels.yaml").write_text(
        "tolerance_ms: 100\nvideos:\n  - path: a.mov\n    impacts: [5.0, 15.0]\n"
    )
    seen = []

    def fake(video, cfg):
        seen.append(video)
        return [5.02]

    total, lines = run(tmp_path / "labels.yaml", Settings(), fake)
    assert seen == [tmp_path / "a.mov"]
    assert (total.true_pos, total.false_neg) == (1, 1)
    assert lines[-1].startswith("total: precision 1.00 recall 0.50")
```

`tests/test_cli.py`:

```python
import pytest

from golf_etl.cli import main
from tests.conftest import make_video_with_clicks


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


@pytest.mark.media
def test_process_writes_a_session_locally(tmp_path, capsys):
    video = make_video_with_clicks(tmp_path / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    assert main(["process", str(video), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "0 swings, 1 rejected" in out
    assert len(list((tmp_path / "out").glob("*/session.md"))) == 1
```

`tests/test_real.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_eval.py tests/test_cli.py tests/test_real.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.eval`, `golf_etl.cli`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/eval.py`:

```python
"""Detection accuracy against hand-labeled impact times."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from golf_etl.config import Settings
from golf_etl.media import probe


@dataclass
class Score:
    true_pos: int = 0
    false_pos: int = 0
    false_neg: int = 0
    errors_ms: list[float] = field(default_factory=list)

    def add(self, other: "Score") -> None:
        self.true_pos += other.true_pos
        self.false_pos += other.false_pos
        self.false_neg += other.false_neg
        self.errors_ms += other.errors_ms

    @property
    def precision(self) -> float:
        found = self.true_pos + self.false_pos
        return self.true_pos / found if found else 1.0

    @property
    def recall(self) -> float:
        actual = self.true_pos + self.false_neg
        return self.true_pos / actual if actual else 1.0

    @property
    def mean_error_ms(self) -> float:
        return sum(self.errors_ms) / len(self.errors_ms) if self.errors_ms else 0.0

    def line(self, name: str) -> str:
        return (
            f"{name}: precision {self.precision:.2f} recall {self.recall:.2f} "
            f"tp {self.true_pos} fp {self.false_pos} fn {self.false_neg} "
            f"mean error {self.mean_error_ms:.0f}ms"
        )


def score(predicted: list[float], truth: list[float], tolerance_s: float) -> Score:
    """Greedy one-to-one match of each true impact to the closest unused prediction."""
    s = Score()
    unused = sorted(predicted)
    for t in sorted(truth):
        best = min(unused, key=lambda p: abs(p - t), default=None)
        if best is not None and abs(best - t) <= tolerance_s:
            unused.remove(best)
            s.true_pos += 1
            s.errors_ms.append(abs(best - t) * 1000)
        else:
            s.false_neg += 1
    s.false_pos = len(unused)
    return s


def run(
    labels_path: Path, cfg: Settings, detect_times: Callable[[Path, Settings], list[float]]
) -> tuple[Score, list[str]]:
    labels = yaml.safe_load(labels_path.read_text())
    tolerance_s = labels.get("tolerance_ms", 150) / 1000
    total, lines = Score(), []
    for entry in labels["videos"]:
        video = (labels_path.parent / entry["path"]).resolve()
        s = score(detect_times(video, cfg), [float(t) for t in entry["impacts"]], tolerance_s)
        total.add(s)
        lines.append(s.line(entry["path"]))
    lines.append(total.line("total"))
    return total, lines


def detected_impacts(video: Path, cfg: Settings) -> list[float]:
    from golf_etl.pipeline import detect_video

    return [s.impact_s for s in detect_video(video, probe(video), cfg).swings]
```

`src/golf_etl/cli.py`:

```python
import argparse
import functools
import hashlib
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.config import Settings

log = logging.getLogger("golf_etl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golf-etl")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process", help="process a local video without Drive")
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, default=Path("out"))
    e = sub.add_parser("eval", help="score detection against labeled impact times")
    e.add_argument("labels", type=Path)
    sub.add_parser("poll-drive", help="process everything in the Drive inbox once")
    a = sub.add_parser("auth", help="mint a Drive refresh token")
    a.add_argument("client_secrets", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Settings.from_env()
    return {"process": cmd_process, "eval": cmd_eval, "poll-drive": cmd_poll, "auth": cmd_auth}[
        args.command
    ](args, cfg)


def cmd_process(args, cfg: Settings) -> int:
    from golf_etl.pipeline import process_video

    with args.video.open("rb") as fh:
        checksum = hashlib.file_digest(fh, "sha256").hexdigest()
    mtime = datetime.fromtimestamp(args.video.stat().st_mtime, UTC)
    result = process_video(
        args.video, args.out, cfg, source_name=args.video.name, checksum=checksum, uploaded_at=mtime
    )
    print(f"{result.session_dir}: {result.swings} swings, {len(result.rejected)} rejected")
    return 0


def cmd_eval(args, cfg: Settings) -> int:
    from golf_etl.eval import detected_impacts, run

    _, lines = run(args.labels, cfg, detected_impacts)
    print("\n".join(lines))
    return 0


def cmd_poll(args, cfg: Settings) -> int:
    from golf_etl.drive.client import Folders
    from golf_etl.drive.google import GoogleDrive, credentials
    from golf_etl.drive.poller import Poller
    from golf_etl.drive.retention import sweep
    from golf_etl.pipeline import process_video

    try:
        creds = credentials(
            os.environ["GOLF_DRIVE_CLIENT_ID"],
            os.environ["GOLF_DRIVE_CLIENT_SECRET"],
            os.environ["GOLF_DRIVE_REFRESH_TOKEN"],
        )
    except KeyError as missing:
        print(f"missing environment variable {missing}", file=sys.stderr)
        return 2
    version = os.environ.get("GOLF_ETL_VERSION", "dev")
    drive = GoogleDrive.connect(creds)
    folders = Folders.resolve(drive, cfg.root_folder)
    process = functools.partial(process_video, cfg=cfg, version=version)
    report = Poller(drive, folders, cfg, process, Path(cfg.scratch_dir), version).run()
    log.info("poll: %s", report)
    swept = sweep(drive, folders, cfg, datetime.now(UTC))
    log.info("sweep: deleted %d, %d bytes in sessions", len(swept.deleted), swept.bytes_after)
    return 0


def cmd_auth(args, cfg: Settings) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    from golf_etl.drive.google import SCOPES

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    print(f"client_id: {creds.client_id}\nrefresh_token: {creds.refresh_token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`README.md`:

````markdown
# golf-etl
Break down golf swing videos into images for AI analysis.

Share a swing video to the Google Drive folder `golf/inbox`. A few minutes later `golf/sessions/<session>/` has a folder per swing with an eight-position sequence sheet, an impact zoom, full-resolution key frames (address, top, impact, finish), and a short clip. Ask claude.ai to review the session for coaching.

How it works is in [docs/design.md](docs/design.md). It runs as a CronJob in [rumstead/homelab](https://github.com/rumstead/homelab) (`kubernetes/manifests/golf-etl`).

## Recording
- Phone on a tripod, whole body and the ball in frame, face-on or down the line.
- Use slo-mo (120fps or more) in daylight. The faster shutter cuts blur, and at 120fps the impact zoom usually catches the club on the ball.
- Sound on. Swings are found by the impact sound.

## Coaching in claude.ai
`claude/skills/golf-swing-analysis/` is the claude.ai skill that knows how to read a session: it finds the newest folder in `golf/sessions`, reads `session.md`, looks at each swing's sequence sheet and impact zoom, asks about ball flight once, and coaches. Zip the folder and upload it in claude.ai's skill settings (replacing an older copy), then ask:

```
review my latest golf session
```

## Run it locally
Everything runs in the container, which has ffmpeg and the pose model.

```sh
podman build -t golf-etl .
podman run --rm --user root -v "$PWD":/work:Z golf-etl process /work/swing.mov --out /work/out
```

## Tests
```sh
podman build --target test -t golf-etl:test .
```
That is what CI runs: ruff and the unit tests.

Real footage lives in `local/` (gitignored) next to a `local/labels.yaml`:

```yaml
tolerance_ms: 17  # two frames at 120fps
videos:
  - path: IMG_4439-a.mov
    impacts: [4.368]  # first frame with the clubface on the ball
```

```sh
podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test python -m pytest -m real
podman run --rm --user root -v "$PWD/local":/w:Z golf-etl eval /w/labels.yaml
```

Thresholds are environment variables (`GOLF_ONSET_DELTA`, `GOLF_IMPACT_AUDIO_LAG_MS`, ...); see `src/golf_etl/config.py`.

## Drive credentials
1. In a personal GCP project, enable the Google Drive API.
2. Configure the OAuth consent screen (External) with the `.../auth/drive` scope and publish it to Production. Refresh tokens for apps left in Testing expire after 7 days.
3. Create an OAuth client of type Desktop app and download `client_secret.json`.
4. Mint a refresh token. The command prints a URL; open it, approve, and it prints the token.
   ```sh
   podman run --rm -it --network host --user root -v "$PWD":/work:Z golf-etl auth /work/client_secret.json
   ```
5. Put `client_id`, `client_secret`, and `refresh_token` in `golf-etl-drive.sops.yaml` in homelab.
````

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_eval.py tests/test_cli.py tests/test_real.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `9 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Run the real-footage tests and eval**

Run: `podman build -t golf-etl . && t python -m pytest -m real -v && podman run --rm --user root -v "$PWD/local":/w:Z golf-etl eval /w/labels.yaml`
Expected: `2 passed`, and eval ends with `total: precision 1.00 recall 1.00 tp 2 fp 0 fn 0` with a mean error of a few ms. If `local/` is missing, copy the two clips back in and recreate `labels.yaml` from the README example (impacts `4.368` for `IMG_4439-a.mov` and `5.260` for `IMG_4439-b.mov`).


- [ ] **Step 6: Look at the frames**

Run: `podman run --rm --user root -v "$PWD/local":/w:Z golf-etl process /w/IMG_4439-b.mov --out /w/out`
Expected: `/w/out/2026-10-03-1240-bdd7988b: 1 swings, 0 rejected`. Open `local/out/2026-10-03-1240-bdd7988b/swing-01/`: natural color (not washed out), `06-sequence.jpg` shows eight labeled positions in order ending in a high finish, and all three rows of `05-impact-zoom.jpg` show the clubface at the yellow ball. Delete `local/out/` afterwards.

- [ ] **Step 7: Commit**

```bash
git add tests/test_eval.py tests/test_cli.py tests/test_real.py src/golf_etl/eval.py src/golf_etl/cli.py README.md
git commit -s -m "feat: add process and eval commands"
```

### Task 11: Poll the Drive inbox with checksum idempotency

**Files:**
- Create: `src/golf_etl/drive/__init__.py`
- Create: `src/golf_etl/drive/client.py`
- Create: `tests/fake_drive.py`
- Test: `tests/test_poller.py`
- Create: `src/golf_etl/drive/poller.py`

**Interfaces:**
- Consumes: `SessionResult` (Task 9), `Settings` (Task 2).
- Produces in `golf_etl.drive.client`: `FOLDER_MIME`, `TAG = "golfEtl"`, `ROOT = "root"`, `DriveFile(id, name, mime_type, parents, created, size=0, sha256=None, md5=None, app_properties={})` with `checksum`, `is_folder`, `is_video`; the `Drive` protocol (`find_folder`, `create_folder`, `list_children`, `list_tagged`, `find_by_property`, `move`, `rename`, `set_properties`, `download`, `upload`, `delete`); `ensure_folder(drive, name, parent_id)`; `Folders(root, inbox, processing, failed, sessions)` with `Folders.resolve(drive, root_name)`.
- Produces in `golf_etl.drive.poller`: `PollReport(recovered, duplicates, published, retrying, failed)` and `Poller(drive, folders, cfg, process, scratch, version).run() -> PollReport`, where `process` is called as `process(local_video, out_root, source_name=, checksum=, uploaded_at=, duplicates=)` and returns a `SessionResult`.
- Produces in `tests/fake_drive.py`: `FakeDrive(now=None)` implementing `Drive`, plus `add_video(name, parent_id, data=b"video", created=None, props=None, mime="video/quicktime")`, `names_in(folder_id)`, `tree()`, and the `deleted` list.

Publishing renames the old session to `.old-<id>` before renaming `.tmp-<id>` into place, then deletes the old one, so a crash at any step leaves a readable session. Recovered videos are retried in the same run.

- [ ] **Step 1: Write the failing tests**

`tests/fake_drive.py`:

```python
"""In-memory Drive for poller and retention tests."""

import hashlib
import itertools
import mimetypes
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.drive.client import FOLDER_MIME, ROOT, DriveFile


class FakeDrive:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.files: dict[str, DriveFile] = {}
        self.content: dict[str, bytes] = {}
        self.deleted: list[str] = []  # names, in delete order
        self._ids = itertools.count(1)

    # helpers for tests
    def add_video(
        self,
        name: str,
        parent_id: str,
        data: bytes = b"video",
        created: datetime | None = None,
        props: Mapping[str, str] | None = None,
        mime: str = "video/quicktime",
    ) -> str:
        fid = f"f{next(self._ids)}"
        self.files[fid] = DriveFile(
            fid,
            name,
            mime,
            (parent_id,),
            created or self.now,
            len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            app_properties=dict(props or {}),
        )
        self.content[fid] = data
        return fid

    def names_in(self, folder_id: str) -> list[str]:
        return sorted(f.name for f in self.list_children(folder_id))

    def path_of(self, fid: str) -> str:
        f = self.files[fid]
        parent = f.parents[0]
        return f.name if parent == ROOT else f"{self.path_of(parent)}/{f.name}"

    def tree(self) -> list[str]:
        return sorted(self.path_of(fid) for fid in self.files)

    # Drive protocol
    def find_folder(self, name, parent_id):
        return next(
            (f.id for f in self.list_children(parent_id) if f.is_folder and f.name == name), None
        )

    def create_folder(self, name, parent_id, props=None):
        fid = f"d{next(self._ids)}"
        self.files[fid] = DriveFile(
            fid, name, FOLDER_MIME, (parent_id,), self.now, app_properties=dict(props or {})
        )
        return fid

    def list_children(self, folder_id):
        return [f for f in self.files.values() if folder_id in f.parents]

    def list_tagged(self):
        return [f for f in self.files.values() if "golfEtl" in f.app_properties]

    def find_by_property(self, parent_id, key, value):
        return [f for f in self.list_children(parent_id) if f.app_properties.get(key) == value]

    def move(self, file_id, new_parent_id):
        self.files[file_id] = replace(self.files[file_id], parents=(new_parent_id,))

    def rename(self, file_id, name):
        self.files[file_id] = replace(self.files[file_id], name=name)

    def set_properties(self, file_id, props):
        f = self.files[file_id]
        self.files[file_id] = replace(f, app_properties={**f.app_properties, **props})

    def download(self, file_id, dest: Path):
        dest.write_bytes(self.content[file_id])

    def upload(self, src: Path, parent_id, props):
        fid = f"f{next(self._ids)}"
        data = src.read_bytes()
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        self.files[fid] = DriveFile(
            fid,
            src.name,
            mime,
            (parent_id,),
            self.now,
            len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            app_properties=dict(props),
        )
        self.content[fid] = data
        return fid

    def delete(self, file_id):
        for child in self.list_children(file_id):
            self.delete(child.id)
        self.deleted.append(self.files[file_id].name)
        del self.files[file_id]
        self.content.pop(file_id, None)
```

`tests/test_poller.py`:

```python
from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.drive.client import Folders
from golf_etl.drive.poller import Poller
from golf_etl.pipeline import SessionResult
from tests.fake_drive import FakeDrive


class FakeProcess:
    """Writes a one-swing session named after the checksum; can be told to fail."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_on: set[str] = set()

    def __call__(
        self, video: Path, out_root: Path, *, source_name, checksum, uploaded_at, duplicates
    ):
        self.calls.append({"name": source_name, "checksum": checksum, "duplicates": duplicates})
        if source_name in self.fail_on:
            raise RuntimeError(f"cannot decode {source_name}")
        sid = f"2026-10-04-1200-{checksum[:8]}"
        swing = out_root / sid / "swing-01"
        swing.mkdir(parents=True)
        (swing / "01-address.jpg").write_bytes(b"jpg" + video.read_bytes())
        (swing / "clip.mp4").write_bytes(b"mp4")
        (out_root / sid / "session.md").write_text(f"# Session {sid}\n")
        return SessionResult(sid, out_root / sid, 1, [])


@pytest.fixture
def env(tmp_path):
    drive = FakeDrive()
    folders = Folders.resolve(drive, "golf")
    process = FakeProcess()

    def run():
        return Poller(drive, folders, Settings(), process, tmp_path / "scratch", "test").run()

    return drive, folders, process, run


def sessions(drive, folders):
    return drive.names_in(folders.sessions)


def test_resolve_creates_the_folder_layout_once():
    drive = FakeDrive()
    first = Folders.resolve(drive, "golf")
    assert Folders.resolve(drive, "golf") == first
    assert drive.tree() == ["golf", "golf/failed", "golf/inbox", "golf/processing", "golf/sessions"]


def test_video_becomes_a_session_and_the_original_is_deleted(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"swing one")
    report = run()
    assert len(report.published) == 1
    sid = report.published[0]
    assert sessions(drive, folders) == [sid]
    assert drive.names_in(folders.inbox) == []
    assert drive.names_in(folders.processing) == []
    assert "IMG_1.MOV" in drive.deleted
    assert f"golf/sessions/{sid}/swing-01/01-address.jpg" in drive.tree()


def test_session_folder_carries_checksum_and_files_are_tagged(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"swing one")
    run()
    session = drive.list_children(folders.sessions)[0]
    assert session.app_properties["sha256"] == process.calls[0]["checksum"]
    assert session.app_properties["sourceName"] == "IMG_1.MOV"
    clip = next(f for f in drive.list_tagged() if f.name == "clip.mp4")
    assert clip.app_properties["kind"] == "clip"
    assert clip.app_properties["sessionFolder"] == session.id


def test_non_video_files_are_left_alone(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox)
    drive.add_video("strike.jpg", folders.inbox, b"photo", mime="image/jpeg")
    run()
    assert drive.names_in(folders.inbox) == ["strike.jpg"]


def test_duplicates_in_one_poll_are_processed_once(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same", created=drive.now)
    drive.add_video(
        "IMG_1 (1).MOV", folders.inbox, b"same", created=drive.now + timedelta(seconds=5)
    )
    report = run()
    assert len(process.calls) == 1
    assert process.calls[0]["duplicates"] == ["IMG_1 (1).MOV"]
    assert report.duplicates == ["IMG_1 (1).MOV"]
    assert len(sessions(drive, folders)) == 1
    assert drive.names_in(folders.inbox) == []


def test_reupload_after_success_replaces_the_session(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    first = drive.list_children(folders.sessions)[0]
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    now = drive.list_children(folders.sessions)
    assert [s.name for s in now] == [first.name]
    assert now[0].id != first.id
    assert len(process.calls) == 2


def test_same_filename_different_content_makes_two_sessions(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"monday")
    drive.add_video("IMG_1.MOV", folders.inbox, b"friday")
    run()
    assert len(sessions(drive, folders)) == 2


def test_first_and_second_failures_go_back_to_the_inbox(env):
    drive, folders, process, run = env
    fid = drive.add_video("bad.MOV", folders.inbox)
    process.fail_on.add("bad.MOV")
    assert run().retrying == ["bad.MOV"]
    assert drive.files[fid].app_properties["attempts"] == "1"
    assert drive.names_in(folders.inbox) == ["bad.MOV"]
    run()
    assert drive.files[fid].app_properties["attempts"] == "2"


def test_third_failure_moves_to_failed_with_an_error_file(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox)
    process.fail_on.add("bad.MOV")
    run()
    run()
    assert run().failed == ["bad.MOV"]
    assert drive.names_in(folders.failed) == ["bad.MOV", "bad.MOV.error.txt"]
    error = next(f for f in drive.list_children(folders.failed) if f.name.endswith(".txt"))
    assert b"cannot decode bad.MOV" in drive.content[error.id]
    assert drive.names_in(folders.inbox) == []


def test_video_left_in_processing_is_recovered_and_retried(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.processing, props={"attempts": "1"})
    report = run()
    assert report.recovered == ["IMG_1.MOV"]
    assert len(report.published) == 1
    assert drive.names_in(folders.processing) == []


def test_recovery_counts_as_an_attempt(env):
    drive, folders, process, run = env
    fid = drive.add_video("IMG_1.MOV", folders.processing)
    process.fail_on.add("IMG_1.MOV")
    run()
    assert drive.files[fid].app_properties["attempts"] == "2"  # recovery, then the failure


def test_recovered_video_past_the_limit_goes_to_failed(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.processing, props={"attempts": "2"})
    assert run().failed == ["IMG_1.MOV"]


def test_reupload_of_a_failed_video_clears_it_and_starts_fresh(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox, b"data")
    process.fail_on.add("bad.MOV")
    run()
    run()
    run()
    process.fail_on.clear()
    fresh = drive.add_video("bad.MOV", folders.inbox, b"data")
    report = run()
    assert drive.names_in(folders.failed) == []
    assert len(report.published) == 1
    assert fresh not in drive.files


def test_crash_after_upload_keeps_the_previous_session(env, monkeypatch):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    old = drive.list_children(folders.sessions)[0]
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    real_rename = drive.rename

    def die_on_swap(file_id, name):
        if name.startswith(".old-"):
            raise RuntimeError("killed")
        real_rename(file_id, name)

    monkeypatch.setattr(drive, "rename", die_on_swap)
    run()
    assert old.id in drive.files
    assert drive.files[old.id].name == old.name
    assert f"golf/sessions/{old.name}/swing-01/01-address.jpg" in drive.tree()


def test_leftover_temp_folder_is_replaced_on_the_next_publish(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    sid = sessions(drive, folders)[0]
    drive.create_folder(f".tmp-{sid}", folders.sessions)
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    assert sessions(drive, folders) == [sid]


def test_one_bad_video_does_not_stop_the_others(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox, b"bad")
    drive.add_video("good.MOV", folders.inbox, b"good")
    process.fail_on.add("bad.MOV")
    report = run()
    assert report.retrying == ["bad.MOV"]
    assert len(report.published) == 1


def test_awkward_filenames_survive_the_round_trip(env):
    drive, folders, process, run = env
    name = "Bob's swing #2 (café).MOV"
    drive.add_video(name, folders.inbox, b"swing")
    process.fail_on.add(name)
    run()
    run()
    run()
    assert drive.names_in(folders.failed) == sorted([name, f"{name}.error.txt"])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_poller.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.drive.client`, `golf_etl.drive.poller`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/drive/__init__.py` (empty file):

```python
```

`src/golf_etl/drive/client.py`:

```python
"""The slice of Google Drive the poller needs. GoogleDrive implements it; tests use a fake."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

FOLDER_MIME = "application/vnd.google-apps.folder"
TAG = "golfEtl"  # appProperties key set on everything the pipeline creates


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...]
    created: datetime
    size: int = 0
    sha256: str | None = None
    md5: str | None = None
    app_properties: Mapping[str, str] = field(default_factory=dict)

    @property
    def checksum(self) -> str | None:
        return self.sha256 or self.md5

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")


class Drive(Protocol):
    def find_folder(self, name: str, parent_id: str) -> str | None: ...
    def create_folder(
        self, name: str, parent_id: str, props: Mapping[str, str] | None = None
    ) -> str: ...
    def list_children(self, folder_id: str) -> list[DriveFile]: ...
    def list_tagged(self) -> list[DriveFile]: ...
    def find_by_property(self, parent_id: str, key: str, value: str) -> list[DriveFile]: ...
    def move(self, file_id: str, new_parent_id: str) -> None: ...
    def rename(self, file_id: str, name: str) -> None: ...
    def set_properties(self, file_id: str, props: Mapping[str, str]) -> None: ...
    def download(self, file_id: str, dest: Path) -> None: ...
    def upload(self, src: Path, parent_id: str, props: Mapping[str, str]) -> str: ...
    def delete(self, file_id: str) -> None:
        """Permanent delete, skipping trash. Folders take their contents with them."""
        ...


ROOT = "root"


def ensure_folder(drive: Drive, name: str, parent_id: str) -> str:
    return drive.find_folder(name, parent_id) or drive.create_folder(name, parent_id)


@dataclass(frozen=True)
class Folders:
    root: str
    inbox: str
    processing: str
    failed: str
    sessions: str

    @classmethod
    def resolve(cls, drive: Drive, root_name: str) -> "Folders":
        root = ensure_folder(drive, root_name, ROOT)
        return cls(
            root,
            *(ensure_folder(drive, n, root) for n in ("inbox", "processing", "failed", "sessions")),
        )
```

`src/golf_etl/drive/poller.py`:

```python
"""One poll of the Drive inbox: recover, dedupe, claim, process, publish, clean up."""

import logging
import shutil
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from golf_etl.config import Settings
from golf_etl.drive.client import TAG, Drive, DriveFile, Folders
from golf_etl.pipeline import SessionResult

log = logging.getLogger(__name__)

# process(video, out_root, *, source_name, checksum, uploaded_at, duplicates) -> SessionResult
Processor = Callable[..., SessionResult]


@dataclass
class PollReport:
    recovered: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    retrying: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class Poller:
    drive: Drive
    folders: Folders
    cfg: Settings
    process: Processor
    scratch: Path
    version: str
    report: PollReport = field(default_factory=PollReport)

    def run(self) -> PollReport:
        self.recover()
        groups: dict[str, list[DriveFile]] = defaultdict(list)
        for f in self.drive.list_children(self.folders.inbox):
            if f.is_video and f.checksum:
                groups[f.checksum].append(f)
        for checksum, copies in groups.items():
            copies.sort(key=lambda f: (f.created, f.id))
            keep, extras = copies[0], copies[1:]
            for extra in extras:
                self.drive.delete(extra.id)
                self.report.duplicates.append(extra.name)
            self.clear_failed(checksum)
            self.handle(keep, checksum, [e.name for e in extras])
        return self.report

    def recover(self) -> None:
        """Anything in processing/ at startup was left by a run that died."""
        for f in self.drive.list_children(self.folders.processing):
            self.report.recovered.append(f.name)
            self.attempt_failed(f, "the run ended while processing this video")

    def clear_failed(self, checksum: str) -> None:
        for f in self.drive.list_children(self.folders.failed):
            if f.checksum == checksum or f.app_properties.get("sha256") == checksum:
                self.drive.delete(f.id)

    def handle(self, video: DriveFile, checksum: str, duplicates: list[str]) -> None:
        self.drive.move(video.id, self.folders.processing)
        log.info("processing %s (%s)", video.name, checksum[:8])
        work = self.scratch / checksum[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            local = work / video.name
            self.drive.download(video.id, local)
            result = self.process(
                local,
                work / "out",
                source_name=video.name,
                checksum=checksum,
                uploaded_at=video.created,
                duplicates=duplicates,
            )
            self.publish(result, checksum, video.name)
            self.drive.delete(video.id)
            self.report.published.append(result.session_id)
            log.info("published %s with %d swings", result.session_id, result.swings)
        except Exception:
            log.exception("processing %s failed", video.name)
            self.attempt_failed(video, traceback.format_exc())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def attempt_failed(self, video: DriveFile, error: str) -> None:
        attempts = int(video.app_properties.get("attempts", "0")) + 1
        self.drive.set_properties(video.id, {"attempts": str(attempts)})
        if attempts < self.cfg.max_attempts:
            self.drive.move(video.id, self.folders.inbox)
            self.report.retrying.append(video.name)
            log.warning("%s will be retried (attempt %d)", video.name, attempts)
            return
        self.drive.move(video.id, self.folders.failed)
        note = self.scratch / f"{video.name}.error.txt"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"{video.name} failed {attempts} times. Last error:\n\n{error}\n")
        try:
            self.drive.upload(note, self.folders.failed, {TAG: "1", "sha256": video.checksum or ""})
        finally:
            note.unlink(missing_ok=True)
        self.report.failed.append(video.name)
        log.error("%s moved to failed/ after %d attempts", video.name, attempts)

    def publish(self, result: SessionResult, checksum: str, source_name: str) -> None:
        """Upload to .tmp-<id>, then swap it in. A crash at any step leaves a readable session."""
        sid = result.session_id
        sessions = self.folders.sessions
        for stale in self.drive.list_children(sessions):
            if stale.name in (f".tmp-{sid}", f".old-{sid}"):
                self.drive.delete(stale.id)
        tmp = self.drive.create_folder(
            f".tmp-{sid}",
            sessions,
            {
                TAG: "1",
                "kind": "session",
                "sha256": checksum,
                "sourceName": source_name,
                "pipelineVersion": self.version,
            },
        )
        self.upload_tree(result.session_dir, tmp, tmp)
        existing = [
            f
            for f in self.drive.find_by_property(sessions, "sha256", checksum)
            if f.id != tmp and not f.name.startswith(".")
        ]
        for old in existing:
            self.drive.rename(old.id, f".old-{sid}")
        self.drive.rename(tmp, sid)
        for old in existing:
            self.drive.delete(old.id)

    def upload_tree(self, local: Path, parent_id: str, session_folder: str) -> None:
        for path in sorted(local.iterdir()):
            props = {TAG: "1", "sessionFolder": session_folder}
            if path.is_dir():
                child = self.drive.create_folder(path.name, parent_id, {**props, "kind": "swing"})
                self.upload_tree(path, child, session_folder)
            else:
                kind = "clip" if path.suffix == ".mp4" else "file"
                self.drive.upload(path, parent_id, {**props, "kind": kind})
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_poller.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `17 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/drive/__init__.py src/golf_etl/drive/client.py tests/fake_drive.py tests/test_poller.py src/golf_etl/drive/poller.py
git commit -s -m "feat: poll the drive inbox with checksum idempotency"
```

### Task 12: Expire Drive outputs and cap session size

**Files:**
- Test: `tests/test_retention.py`
- Create: `src/golf_etl/drive/retention.py`

**Interfaces:**
- Consumes: `Drive`, `DriveFile`, `Folders` (Task 11), `Settings` (Task 2).
- Produces in `golf_etl.drive.retention`: `SweepReport(deleted: list[str], bytes_after: int)` and `sweep(drive, folders, cfg, now) -> SweepReport`.

The sweep reads one tagged listing (`list_tagged`) instead of walking every session folder, so running it every 3 minutes stays cheap.

- [ ] **Step 1: Write the failing tests**

`tests/test_retention.py`:

```python
from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.drive.client import TAG, Folders
from golf_etl.drive.retention import sweep
from tests.fake_drive import FakeDrive

DAY = timedelta(days=1)


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def folders(drive):
    return Folders.resolve(drive, "golf")


def add_session(
    drive, folders, tmp_path: Path, name: str, age: timedelta, clip_bytes=10, frame_bytes=5
) -> str:
    created = drive.now - age
    drive.now, real_now = created, drive.now
    sid = drive.create_folder(name, folders.sessions, {TAG: "1", "kind": "session", "sha256": name})
    swing = drive.create_folder("swing-01", sid, {TAG: "1", "kind": "swing", "sessionFolder": sid})
    for fname, size, kind in (
        ("clip.mp4", clip_bytes, "clip"),
        ("01-address.jpg", frame_bytes, "file"),
    ):
        src = tmp_path / fname
        src.write_bytes(b"x" * size)
        drive.upload(src, swing, {TAG: "1", "kind": kind, "sessionFolder": sid})
    drive.now = real_now
    return sid


def test_young_sessions_are_untouched(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 2 * DAY)
    before = drive.tree()
    assert sweep(drive, folders, Settings(), drive.now).deleted == []
    assert drive.tree() == before


def test_clips_expire_before_frames(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 15 * DAY)
    report = sweep(drive, folders, Settings(), drive.now)
    assert report.deleted == ["clip.mp4"]
    assert "golf/sessions/s1/swing-01/01-address.jpg" in drive.tree()


def test_old_sessions_are_deleted_whole(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "old", 61 * DAY)
    add_session(drive, folders, tmp_path, "new", 1 * DAY)
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == ["new"]


def test_failed_originals_expire_after_three_days(drive, folders):
    drive.add_video("old.MOV", folders.failed, created=drive.now - 4 * DAY)
    drive.add_video("recent.MOV", folders.failed, created=drive.now - 2 * DAY)
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.failed) == ["recent.MOV"]


def test_leftover_temp_folders_are_cleaned_after_a_day(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, ".tmp-s1", 2 * DAY)
    add_session(drive, folders, tmp_path, ".old-s2", 2 * DAY)
    add_session(drive, folders, tmp_path, ".tmp-s3", timedelta(hours=1))
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == [".tmp-s3"]


def test_size_cap_deletes_oldest_sessions_first(drive, folders, tmp_path):
    for i, age in enumerate((5, 4, 3, 2)):
        add_session(drive, folders, tmp_path, f"s{i}", age * DAY, clip_bytes=40, frame_bytes=10)
    cfg = Settings(max_bytes=120)
    report = sweep(drive, folders, cfg, drive.now)
    assert drive.names_in(folders.sessions) == ["s2", "s3"]
    assert report.bytes_after == 100


def test_failed_originals_do_not_evict_sessions(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 2 * DAY, clip_bytes=40, frame_bytes=10)
    drive.add_video("huge.MOV", folders.failed, b"x" * 1000)
    report = sweep(drive, folders, Settings(max_bytes=120), drive.now)
    assert drive.names_in(folders.sessions) == ["s1"]
    assert report.bytes_after == 50
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_retention.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.drive.retention`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/drive/retention.py`:

```python
"""Keep golf/ inside its Drive budget: TTLs first, then a size cap on whole sessions.

The cap counts session files only. Failed originals have their own short TTL, so one large
failed upload cannot push every session out.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from golf_etl.config import Settings
from golf_etl.drive.client import Drive, DriveFile, Folders

LEFTOVER_TTL = timedelta(days=1)


@dataclass
class SweepReport:
    deleted: list[str] = field(default_factory=list)
    bytes_after: int = 0


def sweep(drive: Drive, folders: Folders, cfg: Settings, now: datetime) -> SweepReport:
    report = SweepReport()

    def delete(f: DriveFile) -> None:
        drive.delete(f.id)
        report.deleted.append(f.name)

    for f in drive.list_children(folders.failed):
        if now - f.created > timedelta(days=cfg.failed_ttl_days):
            delete(f)

    tagged = drive.list_tagged()
    sessions = sorted(
        (
            f
            for f in tagged
            if f.app_properties.get("kind") == "session" and folders.sessions in f.parents
        ),
        key=lambda f: f.created,
    )
    gone: set[str] = set()
    for s in sessions:
        leftover = s.name.startswith(".") and now - s.created > LEFTOVER_TTL
        if leftover or now - s.created > timedelta(days=cfg.session_ttl_days):
            delete(s)
            gone.add(s.id)
    sessions = [s for s in sessions if s.id not in gone]

    for f in tagged:
        if (
            f.app_properties.get("kind") == "clip"
            and f.app_properties.get("sessionFolder") not in gone
            and now - f.created > timedelta(days=cfg.clip_ttl_days)
        ):
            delete(f)
            gone.add(f.id)

    by_session: dict[str, int] = defaultdict(int)
    for f in tagged:
        if f.id not in gone and f.app_properties.get("sessionFolder") not in gone:
            by_session[f.app_properties.get("sessionFolder", "")] += f.size
    total = sum(by_session.values())
    for s in sessions:
        if total <= cfg.max_bytes:
            break
        if s.name.startswith("."):
            continue
        delete(s)
        total -= by_session.pop(s.id, 0)
    report.bytes_after = total
    return report
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_retention.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `7 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_retention.py src/golf_etl/drive/retention.py
git commit -s -m "feat: expire drive outputs and cap session size"
```

### Task 13: Talk to Google Drive and wire up poll-drive

**Files:**
- Test: `tests/test_google_drive.py`
- Test: `tests/test_cli.py`
- Create: `src/golf_etl/drive/google.py`

**Interfaces:**
- Consumes: `Drive` protocol and `DriveFile` (Task 11); `cmd_poll` in `cli.py` (Task 10) now resolves its imports.
- Produces in `golf_etl.drive.google`: `SCOPES`, `credentials(client_id, client_secret, refresh_token) -> Credentials`, `GoogleDrive(service)` with `GoogleDrive.connect(creds)`.

`tests/test_cli.py` is the full file now: the same as Task 10 plus `test_poll_drive_without_credentials_exits_2` at the end. Only the import-free parts of GoogleDrive are unit tested, against a recording stand-in for the googleapiclient service; Task 15 covers the real API.

- [ ] **Step 1: Write the failing tests**

`tests/test_google_drive.py`:

```python
"""GoogleDrive against a recording stand-in for the googleapiclient service."""

from datetime import UTC, datetime

from golf_etl.drive.google import GoogleDrive


class Call:
    def __init__(self, recorder, method, kwargs):
        self.recorder, self.method, self.kwargs = recorder, method, kwargs

    def execute(self, num_retries=0):
        self.recorder.calls.append((self.method, self.kwargs))
        responses = self.recorder.responses.get(self.method, [])
        return responses.pop(0) if responses else {"id": "new"}


class Files:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses: dict[str, list[dict]] = {}

    def __getattr__(self, method):
        return lambda **kwargs: Call(self, method, kwargs)


class Service:
    def __init__(self):
        self._files = Files()

    def files(self):
        return self._files


def api_file(i, name="IMG_1.MOV", **extra):
    return {
        "id": f"id{i}",
        "name": name,
        "mimeType": "video/quicktime",
        "parents": ["inbox"],
        "createdTime": "2026-10-04T12:00:00.000Z",
        "size": "42",
        **extra,
    }


def test_list_children_follows_pages_and_maps_fields():
    service = Service()
    service.files().responses["list"] = [
        {"files": [api_file(1, sha256Checksum="abc")], "nextPageToken": "p2"},
        {"files": [api_file(2, appProperties={"attempts": "1"})]},
    ]
    files = GoogleDrive(service).list_children("inbox")
    assert [f.id for f in files] == ["id1", "id2"]
    assert files[0].sha256 == "abc" and files[0].size == 42
    assert files[0].created == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert files[1].app_properties == {"attempts": "1"}
    calls = service.files().calls
    assert calls[0][1]["q"] == "'inbox' in parents and trashed = false"
    assert calls[1][1]["pageToken"] == "p2"


def test_find_by_property_quotes_values():
    service = Service()
    GoogleDrive(service).find_by_property("sessions", "sourceName", "it's.MOV")
    q = service.files().calls[0][1]["q"]
    assert q == (
        "'sessions' in parents and appProperties has "
        "{ key='sourceName' and value='it\\'s.MOV' } and trashed = false"
    )


def test_move_replaces_all_current_parents():
    service = Service()
    service.files().responses["get"] = [{"parents": ["inbox"]}]
    GoogleDrive(service).move("id1", "processing")
    method, kwargs = service.files().calls[-1]
    assert method == "update"
    assert kwargs["addParents"] == "processing" and kwargs["removeParents"] == "inbox"


def test_delete_is_permanent_not_trash():
    service = Service()
    GoogleDrive(service).delete("id1")
    assert service.files().calls == [("delete", {"fileId": "id1"})]


def test_create_folder_sets_properties():
    service = Service()
    fid = GoogleDrive(service).create_folder(".tmp-s1", "sessions", {"golfEtl": "1"})
    assert fid == "new"
    body = service.files().calls[0][1]["body"]
    assert body["parents"] == ["sessions"]
    assert body["appProperties"] == {"golfEtl": "1"}
```

`tests/test_cli.py`:

```python
import pytest

from golf_etl.cli import main
from tests.conftest import make_video_with_clicks


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


@pytest.mark.media
def test_process_writes_a_session_locally(tmp_path, capsys):
    video = make_video_with_clicks(tmp_path / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    assert main(["process", str(video), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "0 swings, 1 rejected" in out
    assert len(list((tmp_path / "out").glob("*/session.md"))) == 1


def test_poll_drive_without_credentials_exits_2(monkeypatch, capsys):
    for key in ("GOLF_DRIVE_CLIENT_ID", "GOLF_DRIVE_CLIENT_SECRET", "GOLF_DRIVE_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert main(["poll-drive"]) == 2
    assert "GOLF_DRIVE_CLIENT_ID" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and watch them fail**

Run: `t python -m pytest tests/test_google_drive.py tests/test_cli.py -v`
Expected: errors or failures from `ModuleNotFoundError` for `golf_etl.drive.google`, which do not exist yet

- [ ] **Step 3: Write the implementation**

`src/golf_etl/drive/google.py`:

```python
"""Drive v3 implementation of the Drive protocol."""

import mimetypes
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from golf_etl.drive.client import FOLDER_MIME, TAG, DriveFile

SCOPES = ["https://www.googleapis.com/auth/drive"]
FIELDS = "id,name,mimeType,parents,createdTime,size,sha256Checksum,md5Checksum,appProperties"
CHUNK = 32 * 1024 * 1024
RETRIES = 3


def credentials(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    return Credentials(
        None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
        token_uri="https://oauth2.googleapis.com/token",
    )


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _to_file(f: dict) -> DriveFile:
    return DriveFile(
        id=f["id"],
        name=f["name"],
        mime_type=f["mimeType"],
        parents=tuple(f.get("parents", [])),
        created=datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00")),
        size=int(f.get("size", 0)),
        sha256=f.get("sha256Checksum"),
        md5=f.get("md5Checksum"),
        app_properties=f.get("appProperties", {}),
    )


class GoogleDrive:
    def __init__(self, service):
        self.files = service.files()

    @classmethod
    def connect(cls, creds: Credentials) -> "GoogleDrive":
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def _list(self, q: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        token = None
        while True:
            resp = self.files.list(
                q=f"{q} and trashed = false",
                spaces="drive",
                pageSize=1000,
                fields=f"nextPageToken,files({FIELDS})",
                pageToken=token,
            ).execute(num_retries=RETRIES)
            out += [_to_file(f) for f in resp.get("files", [])]
            token = resp.get("nextPageToken")
            if not token:
                return out

    def find_folder(self, name: str, parent_id: str) -> str | None:
        found = self._list(
            f"{_quote(parent_id)} in parents and name = {_quote(name)} "
            f"and mimeType = {_quote(FOLDER_MIME)}"
        )
        return found[0].id if found else None

    def create_folder(
        self, name: str, parent_id: str, props: Mapping[str, str] | None = None
    ) -> str:
        body = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
            "appProperties": dict(props or {}),
        }
        return self.files.create(body=body, fields="id").execute(num_retries=RETRIES)["id"]

    def list_children(self, folder_id: str) -> list[DriveFile]:
        return self._list(f"{_quote(folder_id)} in parents")

    def list_tagged(self) -> list[DriveFile]:
        return self._list(f"appProperties has {{ key={_quote(TAG)} and value='1' }}")

    def find_by_property(self, parent_id: str, key: str, value: str) -> list[DriveFile]:
        return self._list(
            f"{_quote(parent_id)} in parents and "
            f"appProperties has {{ key={_quote(key)} and value={_quote(value)} }}"
        )

    def move(self, file_id: str, new_parent_id: str) -> None:
        current = self.files.get(fileId=file_id, fields="parents").execute(num_retries=RETRIES)
        self.files.update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=",".join(current.get("parents", [])),
            fields="id",
        ).execute(num_retries=RETRIES)

    def rename(self, file_id: str, name: str) -> None:
        self.files.update(fileId=file_id, body={"name": name}, fields="id").execute(
            num_retries=RETRIES
        )

    def set_properties(self, file_id: str, props: Mapping[str, str]) -> None:
        self.files.update(fileId=file_id, body={"appProperties": dict(props)}, fields="id").execute(
            num_retries=RETRIES
        )

    def download(self, file_id: str, dest: Path) -> None:
        with dest.open("wb") as fh:
            downloader = MediaIoBaseDownload(
                fh, self.files.get_media(fileId=file_id), chunksize=CHUNK
            )
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=RETRIES)

    def upload(self, src: Path, parent_id: str, props: Mapping[str, str]) -> str:
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(src), mimetype=mime, resumable=True, chunksize=CHUNK)
        body = {"name": src.name, "parents": [parent_id], "appProperties": dict(props)}
        return self.files.create(body=body, media_body=media, fields="id").execute(
            num_retries=RETRIES
        )["id"]

    def delete(self, file_id: str) -> None:
        self.files.delete(fileId=file_id).execute(num_retries=RETRIES)
```

- [ ] **Step 4: Run the tests and the linters**

Run: `t python -m pytest tests/test_google_drive.py tests/test_cli.py -v && t ruff check src tests && t ruff format --check src tests`
Expected: `8 passed`, `All checks passed!`, and no files to reformat

- [ ] **Step 5: Commit**

```bash
git add tests/test_google_drive.py tests/test_cli.py src/golf_etl/drive/google.py
git commit -s -m "feat: talk to google drive and add poll-drive"
```


### Task 14: CI, image push, and Renovate

**Files:**
- Create: `.github/workflows/ci.yaml`
- Create: `renovate.json5`

**Interfaces:**
- Produces: `ghcr.io/rumstead/golf-etl:latest` on every push to `main`, built with `GOLF_ETL_VERSION=<commit sha>`; PRs run the `test` stage only.

- [ ] **Step 1: Write the workflow and Renovate config**

`.github/workflows/ci.yaml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read
  packages: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - name: lint and test
        uses: docker/build-push-action@v7
        with:
          context: .
          target: test
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - uses: docker/login-action@v4
        if: github.event_name == 'push'
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: push latest
        if: github.event_name == 'push'
        uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: ghcr.io/rumstead/golf-etl:latest
          build-args: GOLF_ETL_VERSION=${{ github.sha }}
          cache-from: type=gha
```

`renovate.json5`:

```json5
{
  // Covers requirements*.txt, the Dockerfile base image, and GitHub Actions.
  // The pose model URL in the Dockerfile is pinned by hand.
  $schema: 'https://docs.renovatebot.com/renovate-schema.json',
  extends: [
    'config:recommended',
    ':dependencyDashboard',
  ],
  timezone: 'America/New_York',
  schedule: ['before 6am on monday'],
  prConcurrentLimit: 5,
  labels: ['dependencies'],
  enabledManagers: ['pip_requirements', 'dockerfile', 'github-actions'],
  packageRules: [
    {
      matchUpdateTypes: ['minor', 'patch', 'digest'],
      groupName: 'non-major dependencies',
    },
    {
      // mediapipe pins its own numpy and opencv expectations, move them together.
      matchManagers: ['pip_requirements'],
      matchPackageNames: ['mediapipe', 'numpy', 'opencv-contrib-python'],
      groupName: 'mediapipe stack',
    },
    {
      // A new Python minor needs mediapipe wheels for it first.
      matchManagers: ['dockerfile'],
      matchPackageNames: ['python'],
      matchUpdateTypes: ['major', 'minor'],
      dependencyDashboardApproval: true,
    },
  ],
}
```

- [ ] **Step 2: Validate both**

Run:
```sh
podman run --rm -v "$PWD":/w:Z -w /w docker.io/rhysd/actionlint:latest -no-color .github/workflows/ci.yaml
podman run --rm -v "$PWD":/w:Z -w /w docker.io/library/node:22-slim npx -y --package renovate -- renovate-config-validator --strict renovate.json5
```
Expected: actionlint prints nothing and exits 0; the validator ends with `Config validated successfully`.

- [ ] **Step 3: Commit, push, and open the PR**

```bash
git add .github/workflows/ci.yaml renovate.json5
git commit -s -m "ci: test in the image, push latest, and add renovate"
git push -u origin feat/pipeline
gh pr create --repo rumstead/golf-etl --base main --head feat/pipeline --title "feat: swing pipeline" --body-file pr.md
```
Write `pr.md` first (outside the repo or deleted after) per the writing-as-rumstead skill: what it does, the real-footage results, and the Task 1 spike result.

Expected: the `ci` check passes on the PR (lint and tests inside the `test` stage).

- [ ] **Step 4: After merge, make the image pullable**

1. Watch the `ci` run on `main` push `ghcr.io/rumstead/golf-etl:latest`.
2. In GitHub, Packages, `golf-etl`, Package settings: set visibility to Public (the cluster pulls without a secret, like `lifting-data`).
3. Run: `podman pull ghcr.io/rumstead/golf-etl:latest` while logged out of ghcr.io. Expected: the pull succeeds.

- [ ] **Step 5: Turn on Renovate for the repo**

In GitHub, Settings, Applications, Renovate, Configure: add `rumstead/golf-etl` to the selected repositories. Expected within a day: a "Dependency Dashboard" issue in the repo.

---

### Task 15: Smoke test against real Drive

Needs the OAuth client and refresh token from the README's "Drive credentials" section (homelab task 1.2 needs the same values).

**Files:** none

- [ ] **Step 1: First poll creates the layout**

Run:
```sh
export GOLF_DRIVE_CLIENT_ID=... GOLF_DRIVE_CLIENT_SECRET=... GOLF_DRIVE_REFRESH_TOKEN=...
podman run --rm --user root -e GOLF_DRIVE_CLIENT_ID -e GOLF_DRIVE_CLIENT_SECRET -e GOLF_DRIVE_REFRESH_TOKEN -e GOLF_SCRATCH_DIR=/tmp/scratch ghcr.io/rumstead/golf-etl:latest poll-drive
```
Expected: exit 0, and Drive now has `golf/inbox`, `golf/processing`, `golf/failed`, `golf/sessions`.

- [ ] **Step 2: Process a real upload**

Share `IMG_4439-a.mov` from the phone (or upload `local/IMG_4439-a.mov`) to `golf/inbox`, then run the same command.
Expected: the log shows `published 2026-10-03-1240-19a836d2 with 1 swings`; `golf/sessions/2026-10-03-1240-19a836d2/swing-01/` has the 7 files; the original is gone from `golf/inbox` and is not in Drive's trash.

- [ ] **Step 3: Re-upload replaces, same name different video does not collide**

Upload `IMG_4439-a.mov` again and `IMG_4439-b.mov` once, then run the command.
Expected: `golf/sessions/` holds exactly `2026-10-03-1240-19a836d2` (new folder, same name) and `2026-10-03-1240-bdd7988b`.

- [ ] **Step 4: Hand off to homelab**

Continue with rumstead/homelab#14 (`openspec/changes/add-golf-etl/tasks.md`): encrypt the credentials into `golf-etl-drive.sops.yaml`, add the manifests and Argo CD Application, then repeat Steps 2 and 3 with the CronJob doing the polling.


---

### Task 16: Teach the claude.ai skill to read sessions

The owner's `golf-swing-analysis` skill only knew how to extract frames from an uploaded video. This adds a path for golf-etl sessions so "review my latest golf session" is all the owner has to type. Everything outside Step 1A and one line in Step 3 is the owner's existing skill, with its dashes replaced by colons and periods.

**Files:**
- Create: `claude/skills/golf-swing-analysis/SKILL.md`

- [ ] **Step 1: Write the skill**

`claude/skills/golf-swing-analysis/SKILL.md`:

````markdown
---
name: golf-swing-analysis
description: Analyze golf swings from uploaded videos or from golf-etl sessions in Google Drive. Use this skill whenever the user uploads a golf swing video (.mov, .mp4, or similar) and asks for coaching feedback, swing analysis, what they're doing wrong, how to improve, or any question about their golf swing mechanics. Also use it whenever the user asks to review a golf session, their latest or recent swings, today's range session, or a folder under golf/sessions in Google Drive, even if they don't say golf-etl. Also trigger when the user says they're at the range and wants swing feedback, or when they share multiple swing videos for comparison. Get the frames (from the session folder or by extracting them from the video), identify the camera angle, ask for ball flight context before diagnosing, and deliver structured coaching feedback with prioritized next steps.
---

# Golf Swing Analysis Skill

A structured coaching workflow for analyzing golf swings from any camera angle, with calibrated confidence and prioritized feedback. Frames come either from a golf-etl session in Google Drive or from a video the user uploads.

---

## Core Principles (from real coaching sessions)

1. **Camera angle first**: state what the angle allows and doesn't allow before diagnosing anything
2. **Ball flight is ground truth**: ask for it before making path/face claims
3. **One root cause, not a list**: most swing faults cascade from one problem; identify it
4. **Compensations are not faults**: early extension, flipping, and chicken wing are usually symptoms, not causes
5. **Calibrate confidence**: say "clearly visible", "likely", or "hard to confirm from this angle" on every call
6. **Strengths first**: always lead with what's working before observations
7. **One priority to fix**: give the golfer one thing to work on, not five

---

## Step 1: Get the Frames

There are two sources. Use 1A when the user mentions a session, their latest or recent swings, or Google Drive. Use 1B when they upload a video.

### 1A: From a golf-etl session in Google Drive

golf-etl turns each uploaded range video into a session folder in Google Drive with frames already picked, labeled, and in order.

1. **Find the session.** Search Google Drive for the folders under `golf/sessions`. Folder names start with the capture date and time (`YYYY-MM-DD-HHMM-<id>`), so the newest name is the latest session. Use the session the user names if they name one.
2. **Read `session.md` first.** It lists the swings with their impact times, sounds that were rejected as not being a swing, and how to read the files.
3. **Choose the swings.** All of them if there are 5 or fewer. Otherwise 5 spread across the session (the first, the last, and evenly in between) unless the user asks for specific swings or for all of them.
4. **For each chosen swing, view `swing-NN/06-sequence.jpg` first.** It is the whole swing in one image, read left to right, top row then bottom row: address, takeaway, halfway back, top, transition, impact, follow-through, finish. Each panel is labeled with the position and its time in the video.
5. **Then view `swing-NN/05-impact-zoom.jpg`.** It is the ball area at full resolution for the frames just before, at, and just after impact, top to bottom. Use it for contact, shaft lean, and strike location.
6. **Open `01-address.jpg` to `04-finish.jpg` only when a call needs a closer look.**
7. **Trust the photo over the skeleton.** The green lines are a pose estimate and can be wrong, especially for hands and arms crossing the body.
8. **Look across swings.** Swings in one session are usually the same club and setup. Base the read on what repeats, and point at a single swing (by its folder name, like `swing-03`) only when it differs.

Then continue with Step 2. Do not run ffmpeg for a session; the frames are already chosen.

### 1B: From an uploaded video

Use ffmpeg to extract frames at 8fps from each uploaded video. Scale to 960x540 for speed.

```bash
mkdir -p /home/claude/swing_frames/<video_id>
ffmpeg -i <video_path> \
  -vf "fps=8,scale=960:540" \
  /home/claude/swing_frames/<video_id>/frame_%03d.jpg \
  -y -loglevel quiet
```

Get total frame count and duration to know which frames to pull:
```bash
ffprobe -v quiet -show_entries format=duration -of default <video_path>
```

For each video, view these key positions (adjust frame numbers based on duration):
- Address/setup: early frames
- Takeaway: ~25% through
- Mid-backswing: ~35%
- Top of backswing: ~45-50%
- Early downswing/transition: ~55%
- Impact zone: ~65-70%
- Follow-through: ~80%
- Finish: final frames

---

## Step 2: Identify Camera Angle

Before any diagnosis, determine the camera angle and state it explicitly:

**True Face-On**: Camera pointing directly at the golfer's chest at address. Shows: weight transfer, hip thrust vs. rotation, shoulder tilt, lateral sway. Does NOT show: swing plane, club path, shaft lean at impact.

**True Down-the-Line (DTL)**: Camera pointing directly down the target line from behind. Shows: swing plane, club path above/below plane, over-the-top, flat/steep. Does NOT show: lateral sway, weight transfer direction.

**In-Between / Oblique**: Most range videos. State clearly: "This angle is between face-on and DTL. I can see [X] reliably but [Y] is harder to confirm. Ball flight will help verify."

**High/Low**: Note if the camera is significantly above or below hip height, as this distorts plane readings.

Do NOT make confident plane or path calls from a non-DTL angle. Do NOT make confident weight transfer calls from a non-face-on angle.

---

## Step 3: Ask for Context (Before Diagnosing)

After identifying the angle, ask the golfer these questions before delivering full analysis. This is not optional: ball flight resolves ambiguity that camera angle cannot:

```
Before I give you the full read, a few quick questions:
1. What club is this?
2. What does the ball typically do: pull, slice, push, draw, something else?
3. What have you already been told or are you already working on?
```

If the user is at the range and wants quick feedback, you can deliver a preliminary visual read while waiting for their answers, but clearly label it as "preliminary" and revisit after they answer.

For a golf-etl session, ask once for the whole session, not once per swing.

---

## Step 4: Deliver Structured Analysis

Structure the output as follows:

### Camera Angle & Confidence
State the angle and what it allows/limits in 2-3 sentences.

### Strengths
List 2-4 genuine positives. Look for:
- Setup/posture quality
- Weight transfer direction and completeness
- Lower body sequencing
- Shoulder turn
- Finish position
- Rhythm and tempo

Do not invent positives. If something is genuinely neutral, leave it out.

### Key Observations
List the 1-3 most significant issues visible from this angle. For each:
- State what you see ("the right shoulder fires toward the target at the start of the downswing")
- State your confidence level ("clearly visible from this angle" / "likely but hard to confirm without DTL" / "ball flight suggests this")
- Do NOT state a compensation as a primary fault

**Common fault cascade to watch for (do not invert):**
- Steep/outside-in path → early extension (hips thrust to move low point) → flip/scoop (hand release to close face)
- The path is the cause. Early extension and flip are almost always downstream.
- Over-the-top typically lives in the TRANSITION (right shoulder firing), not the backswing
- Arm-dominated backswing is often overstated. Look for it but confirm before calling it

### Root Cause
Name the single most upstream fault causing the cascade. This is the one thing fixing everything else.

### One Thing to Work On
Give one drill or feel for the root cause. Not two. Not three. One.

Format:
- **The problem**: what's happening
- **The drill**: specific, concrete, executable at the range
- **The feel**: what it should feel like differently
- **What gets better**: what downstream issues will improve as a result

### What to Leave Alone
Explicitly name 1-2 things the golfer should NOT tinker with. This prevents over-coaching and rabbit holes.

---

## Step 5: Handle Pushback

If the golfer pushes back on a call:
1. Go back to the specific frames immediately. Do not defend from memory
2. Re-examine the relevant position with fresh eyes
3. If they're right, correct clearly: "You're right. Looking again at [frame], [corrected read]. I'll pull back what I said about [X]."
4. Update the root cause and priority if the correction changes the picture
5. Never defend an incorrect call to preserve consistency

---

## Angle-Specific Limitations Reference

| What You're Trying to Assess | Best Angle | Fallback |
|------------------------------|-----------|---------|
| Swing plane / club above or below plane | DTL | Ask about ball flight (slice = steep/over-top) |
| Over-the-top move | DTL | Look for low finish, hands exiting left |
| Early extension (hip thrust) | Face-on | DTL shows posture loss through impact |
| Lateral sway on backswing | Face-on | Hard to confirm from DTL |
| Weight transfer direction | Face-on | Look at trail foot at finish |
| Shoulder tilt at address | Face-on | N/A |
| Shaft lean at impact | DTL | N/A |
| Grip | Either close-up | Ask player |

---

## Handicap Context

Calibrate feedback to the player's level:

**20+ handicap**: Path and contact are the priorities. Grip, posture, and pivot only if they're the direct root cause. Don't add swing thoughts; remove them.

**10-20 handicap**: Path is usually solid or near-solid. Look at transition, shaft lean, and release pattern. One ball-striking variable at a time.

**Under 10**: Precision matters. Plane, attack angle, face control, and sequencing timing. Subtle feels are appropriate.

---

## Red Flags (Things Often Misdiagnosed)

- **Flip at impact** → almost always a compensation for steep path, not a standalone fault. Fix the path.
- **Early extension** → almost always a compensation for steep path or loss of posture. Fix the root.
- **Short/low finish** → symptom of outside-in path and/or deceleration, not a cause.
- **Arm-dominated backswing** → often over-called. Confirm with good shoulder turn evidence before diagnosing.
- **C-posture** → check carefully before calling. Camera angle and clothing can make good posture look rounded.
- **Over-the-top** → the move happens in the transition (right shoulder), not necessarily because the takeaway is bad.

---

## Output Tone

- Direct, practical, no fluff
- One coach talking to one golfer, not a written report
- No bullet point overload; use prose where it flows better
- End with a clear "here's what to do at the range today" statement
````

- [ ] **Step 2: Check it**

Run: `grep -cP '[\x{2013}\x{2014}]' claude/skills/golf-swing-analysis/SKILL.md; head -4 claude/skills/golf-swing-analysis/SKILL.md`
Expected: `0`, then the frontmatter with `name: golf-swing-analysis` and the new description mentioning `golf/sessions`.

- [ ] **Step 3: Commit**

```bash
git add claude/skills/golf-swing-analysis/SKILL.md
git commit -s -m "feat: teach the swing analysis skill to read drive sessions"
```

- [ ] **Step 4: Package it for the owner**

Run: `cd claude/skills && python3 -m zipfile -c "$HOME/golf-swing-analysis.zip" golf-swing-analysis/ && python3 -m zipfile -l "$HOME/golf-swing-analysis.zip"`
Expected: the listing shows `golf-swing-analysis/SKILL.md` at the top level of the zip.

- [ ] **Step 5: Owner uploads it in claude.ai**

The owner replaces the existing `golf-swing-analysis` skill in claude.ai's skill settings with `~/golf-swing-analysis.zip`.

- [ ] **Step 6: Coaching round trip**

After Task 15 has put sessions in Drive, in a new claude.ai chat send only: `review my latest golf session`.
Expected: Claude finds the newest folder in `golf/sessions`, reads `session.md`, views the swing's `06-sequence.jpg` and `05-impact-zoom.jpg`, states the camera angle, and asks the ball flight questions once before coaching.
