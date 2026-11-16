"""Body measures from the pose at address, the top and impact, and a session's averages by
grade."""

import math
import warnings

import numpy as np

from golf_etl.frames import Positions
from golf_etl.pose import (
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    PoseTrack,
    toes_direction,
)

NOSE = 0
MIN_VISIBILITY = 0.5  # a joint the pose is less sure of than this is not measured
# session.md column to its key. Moves are in leg lengths from address, angles in degrees.
BODY_COLUMNS = {
    "Head top side": "head_top_side",
    "Head top up": "head_top_up",
    "Head impact side": "head_impact_side",
    "Head impact up": "head_impact_up",
    "Hips impact side": "hips_impact_side",
    "Hands impact side": "hands_impact_side",
    "Hands impact up": "hands_impact_up",
    "Hands top": "hands_top",
    "Spine address": "spine_address",
    "Spine impact": "spine_impact",
}
ANGLES = ("spine_address", "spine_impact")
VIEWS = {1: "down the line", -1: "down the line", 0: "face-on"}


def body_measures(track: PoseTrack, p: Positions) -> dict[str, float | str | None]:
    """How the head, hips and hands moved from address, the hands' height over the shoulders
    at the top, and the spine's lean at address and impact, each None when its joints are not
    seen. Moves are in leg lengths, so they do not depend on where the phone is. Address is
    the median pose over the still stretch before the takeaway: the shoulders start turning
    as the hands start moving, and the spine angle changes a few degrees within 0.2s of it.

    `view` is which way the feet point. Down the line, side is toward the ball and the spine
    angle is the bend toward it. Face-on, side is to the right of the picture and the spine
    angle is the tilt that way."""
    address = _still_pose(track, p.still_from_s, p.address_s)
    top, impact = (_pose(track, t) for t in (p.top_s, p.impact_s))
    toes = toes_direction(address, track.aspect)
    out: dict[str, float | str | None] = dict.fromkeys(BODY_COLUMNS.values())
    out["view"] = VIEWS.get(toes)
    leg = track.leg_length()
    if not leg > 0:
        return out
    side = toes or 1
    scale = np.array([track.aspect * side, -1.0]) / leg

    def point(lm: np.ndarray, joints: list[int], every: bool = True) -> np.ndarray | None:
        seen = ~np.isnan(lm[joints, 0]) & (lm[joints, 2] >= MIN_VISIBILITY)
        if not (seen.all() if every else seen.any()):
            return None
        return lm[joints][seen, :2].mean(axis=0) * scale

    def move(joints: list[int], to: np.ndarray, every: bool = True) -> tuple:
        a, b = point(address, joints, every), point(to, joints, every)
        return (None, None) if a is None or b is None else tuple(float(v) for v in b - a)

    def spine(lm: np.ndarray) -> float | None:
        hips = point(lm, [LEFT_HIP, RIGHT_HIP])
        shoulders = point(lm, [LEFT_SHOULDER, RIGHT_SHOULDER])
        if hips is None or shoulders is None:
            return None
        lean = shoulders - hips
        return math.degrees(math.atan2(lean[0], lean[1]))

    out["head_top_side"], out["head_top_up"] = move([NOSE], top)
    out["head_impact_side"], out["head_impact_up"] = move([NOSE], impact)
    out["hips_impact_side"], _ = move([LEFT_HIP, RIGHT_HIP], impact)
    hands = [LEFT_WRIST, RIGHT_WRIST]
    out["hands_impact_side"], out["hands_impact_up"] = move(hands, impact, every=False)
    if p.top_in_frame:
        at_top = point(top, hands, every=False)
        shoulders = point(top, [LEFT_SHOULDER, RIGHT_SHOULDER])
        if at_top is not None and shoulders is not None:
            out["hands_top"] = float(at_top[1] - shoulders[1])
    out["spine_address"], out["spine_impact"] = spine(address), spine(impact)
    return out


def by_grade(
    grades: list[str | None], rows: dict[str, list[float | None]]
) -> tuple[dict[str, int], dict[str, dict[str, float | None]]]:
    """How many swings have each grade, and each row's mean over the swings of each grade
    that have a value. `rows` maps a name to one value per swing, in swing order."""
    counts = {g: grades.count(g) for g in ("good", "fair", "poor") if g in grades}
    means: dict[str, dict[str, float | None]] = {}
    for name, values in rows.items():
        means[name] = {}
        for g in counts:
            mine = [v for v, grade in zip(values, grades, strict=True) if grade == g]
            mine = [v for v in mine if v is not None]
            means[name][g] = sum(mine) / len(mine) if mine else None
    return counts, means


def _still_pose(track: PoseTrack, from_s: float | None, to_s: float) -> np.ndarray:
    """Landmarks (33, 3), the median of the samples from from_s to to_s, or the pose at to_s
    without a still stretch."""
    if from_s is None:
        return _pose(track, to_s)
    still = (track.times >= from_s) & (track.times <= to_s)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a joint missing in every sample
        return np.nanmedian(track.landmarks[still], axis=0)


def _pose(track: PoseTrack, t: float) -> np.ndarray:
    """Landmarks (33, 3) at t, the median of the samples either side to steady the pose."""
    i = int(np.argmin(np.abs(track.times - t)))
    near = track.landmarks[max(0, i - 1) : i + 2]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a joint missing in every sample
        return np.nanmedian(near, axis=0)
