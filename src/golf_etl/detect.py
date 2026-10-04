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
