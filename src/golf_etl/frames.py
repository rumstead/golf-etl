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


def after_shot_time(
    impact_s: float,
    delay_s: float,
    duration_s: float,
    next_impact_s: float | None = None,
    pre_impact_s: float = 2.5,
) -> float:
    """When to grab the after-shot frame: delay_s after impact, but before the next swing's
    clip window starts and inside the video."""
    t = impact_s + delay_s
    if next_impact_s is not None:
        t = min(t, next_impact_s - pre_impact_s)
    t = min(t, duration_s - 0.05)
    return max(t, impact_s)
