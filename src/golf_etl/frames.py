"""Key swing positions from the pose track, and picking the sharpest nearby frame."""

from dataclasses import dataclass

import cv2
import numpy as np

from golf_etl.pose import LEFT_WRIST, RIGHT_WRIST, PoseTrack

STILL_MIN_S = 0.2
FINISH_AFTER_S = 0.3
RISE_LOOKBACK_S = 1.0  # the takeaway start is looked for this far before the hands reach the hips
RISE_EPS_LEGS = 0.05  # how far the hands rise before the takeaway counts as started
TAKEAWAY_RISE_LEGS = 0.1  # the takeaway position is at least this far above the address hands
ADDRESS_LEVEL_S = 0.2  # the address hand height is the median over this long before address
TOP_VISIBLE_MIN = 0.5  # wrist visibility below this around the top: the hands were not seen


@dataclass(frozen=True)
class Positions:
    address_s: float  # end of the still stretch at address
    takeaway_start_s: float  # the hands start rising from address
    takeaway_s: float  # hands up to the hips going back
    halfway_s: float  # hands up to the shoulders going back
    top_s: float
    transition_s: float  # a fixed time before impact
    impact_s: float
    follow_through_s: float  # a fixed time after impact
    finish_s: float
    top_in_frame: bool = True  # False when the hands are not seen around the top

    def sequence(self) -> list[tuple[str, float]]:
        """Eight positions for the sequence sheet."""
        return [
            ("address", self.address_s),
            ("takeaway", self.takeaway_s),
            ("halfway back", self.halfway_s),
            ("top", self.top_s),
            ("transition", self.transition_s),
            ("impact", self.impact_s),
            ("follow-through", self.follow_through_s),
            ("finish", self.finish_s),
        ]


@dataclass(frozen=True)
class Timing:
    """When the takeaway starts, the top, and impact, in video seconds and clip.mp4 frames."""

    takeaway_start_s: float
    top_s: float
    impact_s: float
    takeaway_start_frame: int
    top_frame: int
    impact_frame: int
    top_in_frame: bool = True  # without it the top, and every duration using it, is a guess

    @property
    def backswing_s(self) -> float:
        return self.top_s - self.takeaway_start_s

    @property
    def downswing_s(self) -> float:
        return self.impact_s - self.top_s

    @property
    def tempo(self) -> float:
        """Backswing time over downswing time."""
        return self.backswing_s / self.downswing_s if self.downswing_s > 0 else float("nan")


def pick_positions(
    track: PoseTrack,
    impact_s: float,
    still_speed: float,
    transition_s: float = 0.1,
    follow_through_s: float = 0.15,
) -> Positions:
    """The backswing positions are body positions, so every swing is compared at the same
    point however its tempo runs: takeaway when the hands first reach the hips (and have risen
    TAKEAWAY_RISE_LEGS from address, for a golfer whose hands address at hip height), halfway
    back when they reach the shoulders. Without hips or shoulders in the pose they fall back to a
    third and two thirds of the time from address to top. Transition and follow-through sit
    transition_s before and follow_through_s after impact, kept between the top and finish."""
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

    back = top_s - address_s
    below_hips = track.hands_below_hips()
    line = _takeaway_line(times, below_hips, address_s, TAKEAWAY_RISE_LEGS * track.leg_length())
    takeaway = _first_above(times, below_hips - line, address_s, top_s)
    takeaway_s = address_s + back / 3 if takeaway is None else takeaway
    halfway = _first_above(times, track.hands_below_shoulders(), takeaway_s, top_s)
    halfway_s = address_s + 2 * back / 3 if halfway is None else max(halfway, takeaway_s)
    near_top = (times >= top_s - 0.1) & (times <= min(top_s + 0.3, impact_s - 0.1))
    visible = track.landmarks[near_top][:, [LEFT_WRIST, RIGHT_WRIST], 2].mean(axis=1)
    top_in_frame = not bool(np.any(np.nan_to_num(visible) < TOP_VISIBLE_MIN))
    rise = None
    if takeaway is not None:
        rise = _rise_start(times, below_hips, RISE_EPS_LEGS * track.leg_length(), takeaway_s)
    return Positions(
        address_s,
        min(address_s, takeaway_s) if rise is None else rise,
        takeaway_s,
        halfway_s,
        top_s,
        min(max(impact_s - transition_s, top_s), impact_s),
        impact_s,
        max(min(impact_s + follow_through_s, finish_s), impact_s),
        finish_s,
        top_in_frame,
    )


def swing_timing(p: Positions, clip_start_s: float, fps: float) -> Timing:
    """Timing of a swing whose clip.mp4 starts at clip_start_s of the video, at fps."""

    def frame(t: float) -> int:
        return round((t - clip_start_s) * fps)

    return Timing(
        p.takeaway_start_s,
        p.top_s,
        p.impact_s,
        frame(p.takeaway_start_s),
        frame(p.top_s),
        frame(p.impact_s),
        p.top_in_frame,
    )


def _rise_start(
    times: np.ndarray, below_hips: np.ndarray, eps: float, takeaway_s: float
) -> float | None:
    """Last time before the hands reach the hips that they were still within eps of their
    lowest point at address, on lightly smoothed heights so pose jitter at address does not
    count as the takeaway. None without a pose to measure it on."""
    smooth = np.convolve(np.pad(below_hips, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    window = (times >= takeaway_s - RISE_LOOKBACK_S) & (times <= takeaway_s) & ~np.isnan(smooth)
    if not window.any() or not eps > 0:
        return None
    low = float(np.max(smooth[window]))
    return float(times[window & (smooth >= low - eps)][-1])


def _takeaway_line(
    times: np.ndarray, below_hips: np.ndarray, address_s: float, rise: float
) -> float:
    """How far below the hips the hands are at the takeaway position: the hip line, or rise
    above the hands at address when that is higher. 0 without a pose at address to measure."""
    at_address = (times >= address_s - ADDRESS_LEVEL_S) & (times <= address_s)
    level = below_hips[at_address & ~np.isnan(below_hips)]
    if not level.size or not rise > 0:
        return 0.0
    return min(0.0, float(np.median(level)) - rise)


def _first_above(
    times: np.ndarray, below: np.ndarray, after_s: float, before_s: float
) -> float | None:
    """First time in [after_s, before_s] the hands are at or above a body line, given how far
    below it they sit. None when they never get there or the line is not in the pose."""
    window = (times >= after_s) & (times <= before_s) & (below <= 0)
    return float(times[window][0]) if window.any() else None


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
