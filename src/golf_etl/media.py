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
