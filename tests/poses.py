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
