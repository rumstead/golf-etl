"""Ball flight off a swing clip: find the ball at rest, follow it after impact, grade it.

The ball is the only small object that sits still before impact and then leaves its spot fast,
so it is found by comparing frames to a per-pixel median without a trained model.
"""

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

from golf_etl.media import VideoInfo, probe, read_frames, scaled_size
from golf_etl.pose import (
    LEFT_ANKLE,
    LEFT_FOOT_INDEX,
    LEFT_HEEL,
    RIGHT_ANKLE,
    RIGHT_FOOT_INDEX,
    RIGHT_HEEL,
    leg_height,
    toes_direction,
)

LONG_EDGE = 960  # points and px/frame are at this scale, half size for a 1080x1920 clip
REF_FPS = 120.0  # per-frame limits below are for this frame rate and scale to the clip's
# At 30fps the ball is out of frame three frames after impact, too few points to follow.
MIN_FPS = 100.0
WINDOW_S = (-0.5, 1.42)  # frames read around impact
# The ball rests a little below the ankle line at address, on every camera spot seen so far.
BAND = (-0.06, 0.07)  # rest spot search rows, frame heights from the address ankles
# Face-on the ball sits between the golfer and the phone, half a leg below the ankles in the
# picture, and the toes point at the lens, so foot length is foreshortened. Leg lengths set
# the search instead.
FACE_ON_BELOW = 0.8  # leg lengths below the address ankles
FACE_ON_BALL = (0.03, 0.12)  # rest blob size limits in leg lengths
FOOT_MARGIN = (0.04, 0.02)  # frame widths and heights around each foot left out of the search
# A ball is about 0.15 of a shoe long; these keep the search the same at any camera distance.
BALL_MIN, BALL_MAX = 0.1, 0.35  # rest blob size limits in foot lengths
DEFAULT_FOOT_PX = 60.0  # foot length at LONG_EDGE when the pose has no heel or toe
GOLFER_GAP = 0.22  # frame widths from the rest spot to the golfer side mask
BODY_MARGIN = 0.05  # frame widths around the golfer left out of the camera check
MOVED_DELTA, MOVED_FRACTION = 40, 0.15  # brightness change and share of the background
BLOB_AREA = (2, 120)  # px of the ball in flight
START_DX, START_DY = 40, (-150, 8)  # how far from the rest spot a chain may start
STEP_DX, STEP_UP, STEP_DOWN = 40, 60, 2  # px per frame
MAX_GAP = 4  # frames
MIN_POINTS, SPEED_POINTS = 6, 10
MIN_RISE = 40  # px over the first five points
REST_STILL = 6  # px; leading chain points closer than this are the ball still at rest


@dataclass(frozen=True)
class Color:
    plane: Callable[[np.ndarray], np.ndarray]  # RGB frame to one uint8 value per pixel
    delta: int  # above the window median by this much in flight
    floor: int  # and at least this high
    rest_delta: int  # before minus after at the rest spot


def _yellowness(rgb: np.ndarray) -> np.ndarray:
    """(R + G) / 2 - B, clipped to uint8. Negative values never pass the thresholds."""
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return np.clip(((r + g) >> 1) - b, 0, 255).astype(np.uint8)


def _whiteness(rgb: np.ndarray) -> np.ndarray:
    """The darkest channel: high only where a pixel is both bright and unsaturated."""
    return np.minimum(np.minimum(rgb[..., 0], rgb[..., 1]), rgb[..., 2])


def _brightness(rgb: np.ndarray) -> np.ndarray:
    return np.maximum(np.maximum(rgb[..., 0], rgb[..., 1]), rgb[..., 2])


COLORS = {
    "yellow": Color(_yellowness, delta=45, floor=60, rest_delta=45),
    "white": Color(_whiteness, delta=40, floor=170, rest_delta=50),
}


@dataclass(frozen=True)
class BallFlight:
    track: str  # "ok", or "lost: <reason>"
    impact_frame: int | None = None
    ball_px_per_frame: float | None = None
    start_dir: float | None = None  # negative is left of the camera axis
    rise_px: int | None = None
    points: tuple[tuple[int, float, float], ...] = ()  # (frame, x, y) at LONG_EDGE
    grade: str | None = None
    ball: str | None = None  # "yellow" or "white"

    @property
    def ok(self) -> bool:
        return self.track == "ok"


@dataclass(frozen=True)
class _Feet:
    boxes: list[np.ndarray]  # pixel (x0, y0, x1, y1) around each foot
    length: float  # heel to toe, pixels
    heel_x: float
    toes: int  # 1 or -1 when the toes point sideways in the picture, 0 when they face the camera

    def behind(self, x: float) -> bool:
        """A ball can't sit behind the heels."""
        return self.toes != 0 and (x - self.heel_x) * self.toes < 0

    def on(self, x: float, y: float) -> bool:
        return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in self.boxes)


@dataclass(frozen=True)
class _Window:
    clip: Path
    info: VideoInfo
    start_s: float
    end_s: float
    rows: int  # frames are read down to this row, the floor below the rest band

    def read(self, plane, edges: int = 0):
        """Frame numbers and `plane` of each frame, plus the brightness of the first and last
        `edges` frames for the camera check."""
        ks, out = [], []
        first: list[np.ndarray] = []
        last: deque[np.ndarray] = deque(maxlen=max(1, edges))
        for t, rgb in read_frames(self.clip, self.info, self.start_s, self.end_s, LONG_EDGE):
            top = rgb[: self.rows]
            ks.append(round(t * self.info.fps))
            out.append(plane(top))
            if edges:
                if len(first) < edges:
                    first.append(_brightness(top))
                last.append(_brightness(top))
        stack = np.stack(out) if out else np.zeros((0, self.rows, 0), np.uint8)
        return np.asarray(ks), stack, first, list(last)


def track_ball(clip: Path, impact_s: float, address: np.ndarray | None) -> BallFlight:
    """Follow the ball off `clip` around `impact_s` (seconds into the clip).

    `address` is the golfer's pose at address (33 landmarks normalized to the frame). The
    feet set where the ball can rest and how big it is.
    """
    if address is None or np.isnan(address[[LEFT_ANKLE, RIGHT_ANKLE], :2]).all():
        return BallFlight("lost: no golfer at address")
    info = probe(clip)
    if info.fps < MIN_FPS:
        return BallFlight(f"lost: clip under {MIN_FPS:g}fps")
    w, h = scaled_size(info.width, info.height, LONG_EDGE)
    px = np.array([w, h], float)
    ankles = address[[LEFT_ANKLE, RIGHT_ANKLE], :2] * px
    ground = float(np.nanmax(ankles[:, 1]))
    feet = _feet(address, px)
    leg = float(leg_height(address)) * h
    face_on = feet.toes == 0 and leg > 0
    if face_on:
        below, size = FACE_ON_BELOW * leg, (FACE_ON_BALL[0] * leg, FACE_ON_BALL[1] * leg)
    else:
        below, size = BAND[1] * h, (BALL_MIN * feet.length, BALL_MAX * feet.length)
    band = (max(0, int(ground + BAND[0] * h)), min(h, int(ground + below)))
    window = _Window(clip, info, max(0.0, impact_s + WINDOW_S[0]), impact_s + WINDOW_S[1], band[1])
    k_impact = round(impact_s * info.fps)

    ks, plane, first, last = window.read(COLORS["yellow"].plane, edges=_frames(30, info.fps))
    if len(ks) == 0:
        return BallFlight("lost: no frames around impact")
    body = (
        int((np.nanmin(address[:, 0]) - BODY_MARGIN) * w),
        int((np.nanmax(address[:, 0]) + BODY_MARGIN) * w),
    )
    if _camera_moved(np.stack(first), np.stack(last), body):
        return BallFlight("lost: camera moved")

    i_impact = int(np.argmin(np.abs(ks - k_impact)))
    name, color = "yellow", COLORS["yellow"]
    rest = _rest_spot(plane, band[0], i_impact, info.fps, color, feet, size)
    if rest is None:
        name, color = "white", COLORS["white"]
        ks, plane, _, _ = window.read(color.plane)
        rest = _rest_spot(plane, band[0], i_impact, info.fps, color, feet, size)
    if rest is None:
        return BallFlight("lost: no ball launched")

    rx, ry = rest
    side = 1 if rx >= float(np.nanmean(ankles[:, 0])) else -1
    cut = int(rx - side * GOLFER_GAP * w)
    x_off = max(0, cut) if side > 0 else 0
    region = plane[:, :, x_off:] if side > 0 else plane[:, :, : max(0, cut)]
    chain = _chain(region, ks, k_impact, (rx - x_off, ry), info.fps, color)
    if not chain:
        # face-on the ball crosses a 120fps frame in two or three frames, already a streak
        if face_on:
            return BallFlight("lost: too fast to follow face-on")
        return BallFlight("lost: ball lost after launch")
    return _metrics([(k, x + x_off, y) for k, x, y in chain], name)


def grade(
    flights: list[BallFlight], good: float, poor: float
) -> tuple[list[BallFlight], float | None]:
    """Grade each tracked swing's ball speed against the session median: good at `good` times
    the median or more, poor under `poor` times it, fair between. Also returns the median."""
    speeds = [f.ball_px_per_frame for f in flights if f.ok and f.ball_px_per_frame is not None]
    if not speeds:
        return list(flights), None
    median = float(np.median(speeds))

    def label(speed: float) -> str:
        return "good" if speed >= good * median else "poor" if speed < poor * median else "fair"

    graded = [replace(f, grade=label(f.ball_px_per_frame)) if f.ok else f for f in flights]
    return graded, median


def summary_line(flights: list[BallFlight], median: float | None) -> str:
    counts = {g: sum(f.grade == g for f in flights) for g in ("good", "fair", "poor")}
    lost = sum(not f.ok for f in flights)
    line = f"Ball flight: {counts['good']} good, {counts['fair']} fair, {counts['poor']} poor"
    line += f", {lost} not tracked"
    if median is not None:
        line += f" (median ball speed {median:.1f} px/frame)"
    return line


def write_ball_json(
    path: Path, flight: BallFlight, *, median: float | None, good: float, poor: float
) -> None:
    """The metrics, the chain and what produced them, so a later run can re-grade without
    decoding video."""
    data = {
        "track": flight.track,
        "ball": flight.ball,
        "impact_frame": flight.impact_frame,
        "ball_px_per_frame": flight.ball_px_per_frame,
        "start_dir": flight.start_dir,
        "rise_px": flight.rise_px,
        "grade": flight.grade,
        "points": [list(p) for p in flight.points],
        "params": {
            "long_edge": LONG_EDGE,
            "window_s": list(WINDOW_S),
            "speed_points": SPEED_POINTS,
            "session_median": median,
            "good": good,
            "poor": poor,
        },
    }
    path.write_text(json.dumps(data) + "\n")


def _feet(address: np.ndarray, px: np.ndarray) -> _Feet:
    boxes, lengths, heels = [], [], []
    margin = np.array(FOOT_MARGIN) * px
    for ankle, heel, toe in (
        (LEFT_ANKLE, LEFT_HEEL, LEFT_FOOT_INDEX),
        (RIGHT_ANKLE, RIGHT_HEEL, RIGHT_FOOT_INDEX),
    ):
        pts = address[[ankle, heel, toe], :2] * px
        pts = pts[~np.isnan(pts).any(axis=1)]
        if len(pts):
            boxes.append(np.concatenate([pts.min(axis=0) - margin, pts.max(axis=0) + margin]))
        step = (address[toe, :2] - address[heel, :2]) * px
        if not np.isnan(step).any():
            lengths.append(float(np.hypot(*step)))
            heels.append(float(address[heel, 0] * px[0]))
    if not lengths:
        return _Feet(boxes, DEFAULT_FOOT_PX, 0.0, 0)
    toes = toes_direction(address, px[0] / px[1])
    return _Feet(boxes, float(np.median(lengths)), float(np.mean(heels)), toes)


def _camera_moved(first: np.ndarray, last: np.ndarray, golfer: tuple[int, int]) -> bool:
    """The background outside the golfer looks different at the end of the window."""
    changed = np.abs(np.median(first, axis=0) - np.median(last, axis=0)) > MOVED_DELTA
    lo, hi = max(0, golfer[0]), max(0, golfer[1])
    changed[:, lo:hi] = False
    outside = changed.size - changed[:, lo:hi].size
    return outside > 0 and changed.sum() / outside > MOVED_FRACTION


def _blobs(mask: np.ndarray, area: tuple[float, float]) -> list[tuple[float, float, int]]:
    n, _, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    return [
        (float(centers[j][0]), float(centers[j][1]), int(stats[j][4]))
        for j in range(1, n)
        if area[0] <= stats[j][4] <= area[1]
    ]


def _rest_spot(
    plane: np.ndarray,
    top: int,
    i_impact: int,
    fps: float,
    color: Color,
    feet: _Feet,
    size: tuple[float, float],
) -> tuple[float, float] | None:
    """Where a ball-sized (`size` px across, min and max), ball-colored spot sat before impact
    and was gone after it."""
    band = plane[:, top:]
    pre = band[: max(1, i_impact - _frames(12, fps))]
    post = band[i_impact + _frames(36, fps) :]
    if len(post) == 0:
        return None
    before = np.median(pre, axis=0)
    after = np.median(post, axis=0)
    gone = ((before - after > color.rest_delta) & (before > color.floor)).astype(np.uint8)
    n, _, stats, centers = cv2.connectedComponentsWithStats(gone)
    found = []
    for j in range(1, n):
        bw, bh, area = stats[j][2], stats[j][3], stats[j][4]
        x, y = float(centers[j][0]), float(centers[j][1]) + top
        round_enough = 0.5 <= bw / bh <= 2 and area >= 0.4 * bw * bh
        sized = area >= size[0] ** 2 and max(bw, bh) <= size[1]
        if round_enough and sized and not feet.on(x, y) and not feet.behind(x):
            found.append((area, x, y))
    if not found:
        return None
    _, x, y = max(found)
    return x, y


def _chain(
    plane: np.ndarray,
    ks: np.ndarray,
    k_impact: int,
    rest: tuple[float, float],
    fps: float,
    color: Color,
) -> list[tuple[int, float, float]]:
    """Longest run of new ball-colored blobs that starts at the rest spot and climbs."""
    per = REF_FPS / fps  # px limits per frame grow when frames are further apart
    med = np.median(plane, axis=0).astype(np.int16)
    found = []
    for k, frame in zip(ks, plane, strict=True):
        mask = (frame.astype(np.int16) - med > color.delta) & (frame > color.floor)
        blobs = _blobs(mask, BLOB_AREA)
        if blobs:
            found.append((int(k), blobs))
    rx, ry = rest
    lo, hi = k_impact - _frames(4, fps), k_impact + _frames(10, fps)
    best: list[tuple[int, float, float]] = []
    for si, (k0, blobs0) in enumerate(found):
        if not lo <= k0 <= hi:
            continue
        starts = [
            b
            for b in blobs0
            if abs(b[0] - rx) <= START_DX and ry + START_DY[0] <= b[1] <= ry + START_DY[1]
        ]
        for b0 in sorted(starts, key=lambda b: abs(b[0] - rx) + abs(b[1] - ry)):
            chain = [(k0, b0[0], b0[1])]
            for k, blobs in found[si + 1 :]:
                if k - chain[-1][0] > _frames(MAX_GAP, fps):
                    break
                _, px, py = chain[-1]
                near = [
                    b
                    for b in blobs
                    if py - STEP_UP * per <= b[1] <= py + STEP_DOWN * per
                    and abs(b[0] - px) < STEP_DX * per
                ]
                if near:
                    b = min(near, key=lambda b: abs(b[0] - px) + abs(b[1] - py))
                    chain.append((k, b[0], b[1]))
            chain = _drop_rest(chain)
            n = min(len(chain) - 1, 5)
            rises = len(chain) >= MIN_POINTS and chain[0][2] - chain[n][2] > MIN_RISE
            if rises and len(chain) > len(best):
                best = chain
    return best


def _drop_rest(chain: list[tuple[int, float, float]]) -> list[tuple[int, float, float]]:
    """Keep only the last of a chain's leading points at the rest spot."""
    while len(chain) > 1 and np.hypot(chain[1][1] - chain[0][1], chain[1][2] - chain[0][2]) < (
        REST_STILL
    ):
        chain = chain[1:]
    return chain


def _metrics(chain: list[tuple[int, float, float]], ball: str) -> BallFlight:
    k0, x0, y0 = chain[0]
    k1, x1, y1 = chain[min(len(chain) - 1, SPEED_POINTS)]
    return BallFlight(
        "ok",
        impact_frame=int(k0),
        ball_px_per_frame=round(float(np.hypot(x1 - x0, y1 - y0) / max(1, k1 - k0)), 1),
        start_dir=round(float((x1 - x0) / max(1.0, y0 - y1)), 2),
        rise_px=round(float(y0 - chain[-1][2])),
        points=tuple((int(k), round(x, 1), round(y, 1)) for k, x, y in chain),
        ball=ball,
    )


def _frames(ref_frames: int, fps: float) -> int:
    """A frame count tuned at REF_FPS, at the clip's frame rate."""
    return max(1, round(ref_frames * fps / REF_FPS))
