"""Synthetic pose tracks for tests: hands at address, up to the top, down through impact."""

from collections.abc import Callable, Mapping

import numpy as np

from golf_etl.pose import (
    LEFT_ANKLE,
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    NUM_LANDMARKS,
    RIGHT_ANKLE,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    PoseTrack,
)


def _hand_y(rel: np.ndarray, down_s: float = 0.4) -> np.ndarray:
    """Hand height in normalized y (smaller is higher) at times relative to impact: 0.6 at
    address, 0.2 at the top, accelerating down into impact, 0.15 at the finish."""
    y = np.full(rel.shape, 0.6)
    back = (rel > -1.2) & (rel <= -down_s)
    y[back] = 0.6 - 0.4 * (rel[back] + 1.2) / (1.2 - down_s)
    down = (rel > -down_s) & (rel <= 0.0)
    y[down] = 0.2 + 0.4 * ((rel[down] + down_s) / down_s) ** 3
    through = (rel > 0.0) & (rel <= 0.8)
    y[through] = 0.6 - 0.45 * np.sin(np.pi / 2 * rel[through] / 0.8)
    y[rel > 0.8] = 0.15
    return y


def _track(times: np.ndarray, hand_y: np.ndarray) -> PoseTrack:
    """A golfer with shoulders at 0.3, hips at 0.55 and ankles at 0.9 (legs 0.35 frame heights
    long), so the hands at address (0.6) sit just below the hips. Going back they reach the
    hips 0.1s after takeaway and the shoulders 0.6s before impact."""
    lm = np.zeros((len(times), NUM_LANDMARKS, 3))
    lm[:, :, 2] = 0.9
    for idx in (LEFT_WRIST, RIGHT_WRIST):
        lm[:, idx, 0] = 0.5
        lm[:, idx, 1] = hand_y
    lm[:, LEFT_SHOULDER, :2] = (0.45, 0.3)
    lm[:, RIGHT_SHOULDER, :2] = (0.55, 0.3)
    lm[:, LEFT_HIP, :2] = (0.47, 0.55)
    lm[:, RIGHT_HIP, :2] = (0.53, 0.55)
    lm[:, LEFT_ANKLE, :2] = (0.45, 0.9)
    lm[:, RIGHT_ANKLE, :2] = (0.55, 0.9)
    return PoseTrack(times, lm, aspect=16 / 9)


def swing_track(
    impact_s: float,
    fps: float = 60.0,
    before: float = 2.5,
    after: float = 1.5,
    missing: tuple[float, float] | None = None,
) -> PoseTrack:
    """Still at address until impact-1.2s, top at impact-0.4s, fastest at impact, still by +0.8s."""
    times = np.arange(impact_s - before, impact_s + after, 1 / fps)
    track = _track(times, _hand_y(times - impact_s))
    if missing:
        track.landmarks[(times >= missing[0]) & (times < missing[1])] = np.nan
    return track


def session_track(
    impacts: list[float], span_s: float, down_s: Mapping[float, float] | None = None
) -> PoseTrack:
    """A whole session at 60fps: at address, a swing at each impact, the finish held for 0.7s,
    then back to address over 1.5s. down_s sets a swing's downswing time (0.4s default);
    shorter is faster."""
    times = np.arange(0.0, span_s, 1 / 60)
    y = np.full(times.shape, 0.6)
    for impact in impacts:
        rel = times - impact
        swing = (rel > -1.2) & (rel <= 1.5)
        y[swing] = _hand_y(rel[swing], (down_s or {}).get(impact, 0.4))
        back = (rel > 1.5) & (rel <= 3.0)
        y[back] = 0.15 + 0.45 * (rel[back] - 1.5) / 1.5
    return _track(times, y)


def windows(track: PoseTrack) -> Callable[[float, float], PoseTrack]:
    """A track_for that cuts [start, end) out of a session track, as the pipeline decodes it."""

    def track_for(start: float, end: float) -> PoseTrack:
        keep = (track.times >= start) & (track.times < end)
        return PoseTrack(track.times[keep], track.landmarks[keep], track.aspect)

    return track_for


def still_track(center_s: float, fps: float = 60.0, span: float = 4.0) -> PoseTrack:
    """Someone standing still (the user waiting while a neighbor hits)."""
    times = np.arange(center_s - span / 2, center_s + span / 2, 1 / fps)
    return _track(times, np.full(times.shape, 0.6))


def empty_track(center_s: float, fps: float = 60.0) -> PoseTrack:
    times = np.arange(center_s - 2, center_s + 2, 1 / fps)
    return PoseTrack(times, np.full((len(times), NUM_LANDMARKS, 3), np.nan), aspect=16 / 9)


def fast_finish_track(impact_s: float, fps: float = 60.0) -> PoseTrack:
    """A swing whose hands move faster in the follow-through than at impact, as some camera
    angles see it."""
    track = swing_track(impact_s, fps=fps)
    rel = track.times - impact_s
    swoop = (rel > 0.6) & (rel < 0.9)
    dip = 0.5 * np.sin(np.pi * (rel[swoop] - 0.6) / 0.3)
    track.landmarks[swoop, LEFT_WRIST, 1] -= dip
    track.landmarks[swoop, RIGHT_WRIST, 1] -= dip
    return track


def scaled(track: PoseTrack, factor: float) -> PoseTrack:
    """The same motion with the golfer factor times as tall in frame, feet fixed, as when the
    phone is moved closer."""
    lm = track.landmarks.copy()
    lm[:, :, 1] = 0.9 - (0.9 - lm[:, :, 1]) * factor
    lm[:, :, 0] = 0.5 + (lm[:, :, 0] - 0.5) * factor
    return PoseTrack(track.times, lm, track.aspect)


def waggle_track(center_s: float, hand_y: float, fps: float = 60.0) -> PoseTrack:
    """Standing still with the hands bobbing fast (3Hz, 0.1 frame heights) around hand_y."""
    track = still_track(center_s, fps=fps)
    bob = hand_y + 0.1 * np.sin(2 * np.pi * 3 * track.times)
    track.landmarks[:, LEFT_WRIST, 1] = bob
    track.landmarks[:, RIGHT_WRIST, 1] = bob
    return track
