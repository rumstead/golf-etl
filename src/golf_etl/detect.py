"""Swing detection: audio onsets confirmed by fast hands in the pose track."""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from golf_etl.audio import Onset
from golf_etl.config import Settings
from golf_etl.pose import PoseTrack

# Pose around each sound covers the confirm window plus this much on each side, so the hand
# speed at the window edges is not an edge-of-track estimate.
CONFIRM_MARGIN_S = 0.2

TrackFor = Callable[[float, float], PoseTrack]  # pose track for [start_s, end_s) of the video


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


@dataclass(frozen=True)
class Check:
    offset_ms: float | None  # fastest hands near the sound minus the sound; None when rejected
    speed: float  # that hand speed, leg lengths per second
    reason: str  # why it was rejected, "" when confirmed


def confirm(track: PoseTrack, onset_s: float, cfg: Settings) -> Check:
    """Confirmed when the hands are moving fast within confirm_window_ms of the sound and sit
    about level with the hips at the sound, as they do at impact.

    Speed and height are measured in leg lengths so a close phone does not make a waggle look
    fast. Where the hands are fastest overall does not matter: some camera angles see the
    follow-through faster than impact.
    """
    near = np.abs(track.times - onset_s) <= cfg.confirm_window_ms / 1000
    speed = track.hand_speed()[near]
    if speed.size == 0 or np.all(np.isnan(speed)):
        return Check(None, 0.0, "no pose found")
    leg = track.leg_length()
    if not leg > 0:
        return Check(None, 0.0, "legs not in frame")
    i = int(np.nanargmax(speed))
    peak_speed = float(speed[i]) / leg
    if peak_speed < cfg.confirm_min_speed:
        return Check(None, peak_speed, f"hands too slow ({peak_speed:.1f} leg lengths/s)")
    below = float(track.hands_below_hips()[int(np.argmin(np.abs(track.times - onset_s)))]) / leg
    if np.isnan(below):
        return Check(None, peak_speed, "no pose at the sound")
    if below < cfg.confirm_hands_min:
        return Check(None, peak_speed, f"hands above the hips ({-below:.2f} leg lengths)")
    if below > cfg.confirm_hands_max:
        return Check(None, peak_speed, f"hands below the hips ({below:.2f} leg lengths)")
    return Check((float(track.times[near][i]) - onset_s) * 1000, peak_speed, "")


def detect_swings(
    onsets: list[Onset], track_for: TrackFor, duration_s: float, cfg: Settings
) -> Detection:
    """Confirm every sound on a short pose window, then keep the fastest swing within any
    onset_min_gap_s span (the whole video for a short clip) and pose its full clip window.

    A louder sound never hides a quieter one here: the finish, a club tap, and a strike can
    all land within a few seconds, and only the pose tells them apart.
    """
    rejected: list[Rejected] = []
    confirmed: list[tuple[Onset, Check]] = []
    half = cfg.confirm_window_ms / 1000 + CONFIRM_MARGIN_S
    for onset in onsets:
        check = confirm(track_for(onset.time_s - half, onset.time_s + half), onset.time_s, cfg)
        if check.offset_ms is None:
            rejected.append(Rejected(onset.time_s, onset.strength, check.reason))
        else:
            confirmed.append((onset, check))

    if duration_s < cfg.short_clip_s:
        gap, reason = float("inf"), "single-swing clip keeps the fastest"
    else:
        gap, reason = cfg.onset_min_gap_s, f"within {cfg.onset_min_gap_s:g}s of a faster swing"
    kept: list[tuple[Onset, Check]] = []
    for onset, check in sorted(confirmed, key=lambda c: c[1].speed, reverse=True):
        if all(abs(onset.time_s - k.time_s) >= gap for k, _ in kept):
            kept.append((onset, check))
        else:
            rejected.append(Rejected(onset.time_s, onset.strength, reason))

    swings = [
        Swing(
            onset.time_s,
            onset.strength,
            check.offset_ms,
            track_for(onset.time_s - cfg.pre_impact_s, onset.time_s + cfg.post_impact_s),
        )
        for onset, check in sorted(kept, key=lambda c: c[0].time_s)
    ]
    rejected.sort(key=lambda r: r.time_s)
    return Detection(swings, rejected)
