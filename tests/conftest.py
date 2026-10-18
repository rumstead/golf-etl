import os
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
    path: Path,
    clicks: list[float],
    seconds: float = 10.0,
    size: str = "1280x720",
    rate: int | str = 30,
) -> Path:
    """Test pattern video whose audio has impact-like clicks at the given times. `rate` is
    frames per second, or a ratio like "30000/1001"."""
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


def git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, check=True, text=True).stdout


@pytest.fixture
def no_host_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def remote(tmp_path, no_host_git_config):
    """A local bare repo that serves blob-filtered clones over file://, like GitHub does."""
    path = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(path))
    git("-C", str(path), "config", "uploadpack.allowFilter", "true")
    return path


def tree(remote) -> set[str]:
    """Every file path on the remote's main branch."""
    out = git("--git-dir", str(remote), "ls-tree", "-r", "-l", "main")
    return {line.split("\t", 1)[1] for line in out.splitlines()}
