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
from golf_etl.frames import after_shot_time, pick_positions, sharpest
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
    impacts = [s.impact_s for s in detection.swings]
    for n, swing in enumerate(detection.swings, 1):
        next_impact = impacts[n] if n < len(impacts) else None
        write_swing(video, info, swing, session_dir / f"swing-{n:02d}", cfg, next_impact)
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


def write_swing(
    video: Path,
    info: VideoInfo,
    swing: Swing,
    out: Path,
    cfg: Settings,
    next_impact_s: float | None = None,
) -> None:
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

    # On a simulator or launch monitor the screen shows this shot's numbers only seconds
    # after impact; every earlier frame shows the previous shot's.
    after = after_shot_time(
        swing.impact_s, cfg.after_shot_s, info.duration_s, next_impact_s, cfg.pre_impact_s
    )
    window = list(read_frames(video, info, after, after + 1.5 * dt))
    if not window:
        raise RuntimeError(f"no frames decoded for the after-shot frame at {after:.2f}s")
    ft, img = window[0]
    frame = render.fit(img, cfg.frame_long_edge, cfg.frame_max_pixels)
    render.save_jpeg(
        render.label(frame, f"after shot {ft:.2f}s"), out / "07-after-shot.jpg", cfg.jpeg_quality
    )

    encode_clip(
        video,
        info,
        swing.impact_s - cfg.pre_impact_s,
        swing.impact_s + cfg.post_impact_s,
        out / "clip.mp4",
        cfg.clip_long_edge,
        cfg.clip_crf,
    )
