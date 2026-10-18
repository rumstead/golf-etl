"""One video in, one session folder out."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from golf_etl import render
from golf_etl.audio import SAMPLE_RATE, Onset, find_onsets
from golf_etl.ball import BallFlight, grade, track_ball, write_ball_json
from golf_etl.config import Settings
from golf_etl.detect import Detection, Rejected, Swing, detect_swings
from golf_etl.frames import Timing, after_shot_time, pick_positions, sharpest, swing_timing
from golf_etl.media import VideoInfo, encode_clip, probe, read_audio, read_frames
from golf_etl.pose import PoseEstimator, write_pose_csv
from golf_etl.session import SessionSummary, session_id, video_time, write_session_md

Detector = Callable[[Path, VideoInfo, Settings], Detection]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    session_dir: Path
    swings: int
    rejected: list[Rejected]


def detect_video(video: Path, info: VideoInfo, cfg: Settings) -> Detection:
    onsets: list[Onset] = []
    if info.has_audio:
        samples = read_audio(video, info, SAMPLE_RATE)
        onsets = find_onsets(
            samples,
            SAMPLE_RATE,
            cfg.onset_highpass_hz,
            cfg.onset_delta,
            cfg.impact_audio_lag_ms / 1000,
        )
    estimator = PoseEstimator(cfg.pose_model_path)

    def track_for(start_s: float, end_s: float):
        frames = read_frames(video, info, start_s, end_s, cfg.pose_long_edge, cfg.pose_max_fps)
        return estimator.track(frames)

    return detect_swings(onsets, track_for, info.duration_s, cfg)


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
    balls, timings = [], []
    for n, swing in enumerate(detection.swings, 1):
        next_impact = impacts[n] if n < len(impacts) else None
        flight, timing = write_swing(
            video, info, swing, session_dir / f"swing-{n:02d}", cfg, next_impact
        )
        balls.append(flight)
        timings.append(timing)
    balls, ball_median = grade(balls, cfg.ball_good, cfg.ball_poor)
    for n, flight in enumerate(balls, 1):
        write_ball_json(
            session_dir / f"swing-{n:02d}" / "ball.json",
            flight,
            median=ball_median,
            good=cfg.ball_good,
            poor=cfg.ball_poor,
        )
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
            balls=balls,
            ball_median=ball_median,
            timings=timings,
            storage=cfg.storage,
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
) -> tuple[BallFlight, Timing]:
    """Write the swing's frames and clip, follow the ball in the clip, and time the swing."""
    out.mkdir(parents=True, exist_ok=True)
    positions = pick_positions(
        swing.track, swing.impact_s, cfg.still_speed, cfg.transition_s, cfg.follow_through_s
    )
    dt = 1 / info.fps
    picked: dict[str, tuple[float, np.ndarray]] = {}
    impact_frames: list[tuple[int, np.ndarray]] = []  # (offset from impact, frame)
    for name, t in positions.sequence():
        if name == "impact":
            # two spare frames a side, so the frame nearest impact never sits at the edge
            window = list(read_frames(video, info, t - 2.5 * dt, t + 2.5 * dt))
            if not window:
                raise RuntimeError(f"no frames decoded around impact at {t:.2f}s")
            i = int(np.argmin([abs(ft - t) for ft, _ in window]))
            impact_frames = [(j - i, img) for j, (_, img) in enumerate(window) if abs(j - i) <= 1]
            picked[name] = window[i]
        else:
            r = cfg.sharpness_radius + 0.5
            window = list(read_frames(video, info, t - r * dt, t + r * dt))
            if not window:
                raise RuntimeError(f"no frames decoded around {name} at {t:.2f}s")
            picked[name] = sharpest(window)

    address_pose = swing.track.nearest(positions.address_s)
    names = {-1: "impact -1", 0: "impact", 1: "impact +1"}
    strip = np.vstack(
        [render.label(render.zoom_crop(img, address_pose), names[k]) for k, img in impact_frames]
    )
    zoom = render.fit(strip, cfg.frame_long_edge, cfg.frame_max_pixels)
    render.save_jpeg(zoom, out / "05-impact-zoom.jpg", cfg.jpeg_quality)
    render.save_jpeg_within(
        lambda edge: render.fit(strip, edge), out / "05-impact-zoom-small.jpg", cfg.small_max_bytes
    )

    overlays, labels = [], []
    for name, _ in positions.sequence():
        ft, img = picked[name]
        small = render.fit(img, cfg.frame_long_edge // 2)
        overlays.append(render.draw_pose(small, swing.track.nearest(ft)))
        labels.append(f"{name} {video_time(ft)}")
    sheet = render.grid(overlays, labels, 4, cfg.frame_long_edge, cfg.frame_max_pixels)
    render.save_jpeg(sheet, out / "06-sequence.jpg", cfg.jpeg_quality)
    render.save_jpeg_within(
        lambda edge: render.grid(overlays, labels, 4, edge, cfg.frame_max_pixels),
        out / "06-sequence-small.jpg",
        cfg.small_max_bytes,
    )

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
    text = f"after shot {video_time(ft)}"
    render.save_jpeg(render.label(frame, text), out / "07-after-shot.jpg", cfg.jpeg_quality)
    render.save_jpeg_within(
        lambda edge: render.label(render.fit(frame, edge), text),
        out / "07-after-shot-small.jpg",
        cfg.small_max_bytes,
    )

    clip_start = max(0.0, swing.impact_s - cfg.pre_impact_s)
    encode_clip(
        video,
        info,
        clip_start,
        swing.impact_s + cfg.post_impact_s,
        out / "clip.mp4",
        cfg.clip_long_edge,
        cfg.clip_crf,
    )
    write_pose_csv(swing.track, out / "pose.csv", swing.impact_s, clip_start, info.fps)
    timing = swing_timing(positions, clip_start, info.fps)
    try:
        flight = track_ball(out / "clip.mp4", swing.impact_s - clip_start, address_pose)
    except Exception:
        # a tracking bug costs this swing its grade, not the whole session
        log.exception("ball tracking failed for %s", out.name)
        flight = BallFlight("lost: tracking error")
    return flight, timing
