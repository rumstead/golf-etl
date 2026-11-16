"""Where along the face the ball was struck, from the frames around impact seen down the line.

The mat is plain, so in the frame the club meets the ball the shaft is a light, straight line
on it. The shaft's line, carried down to the ball's center row, is the hosel's place there,
and the ball's center sits that many ball diameters from it toward the toe. The diameter is
the session's median, since one ball is only 12 to 22px across and a pixel off it moves the
number by a tenth. The camera sees the face at a slant, so the number ranks the swings of one
session and is not inches on the face."""

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from golf_etl.ball import RestSpot

BEFORE_S = 0.15  # the ball is compared against the frame this long before impact
# Contact is impact or the frame after it, never earlier: on 36 swings of four sim sessions
# checked by eye it was one of the two every time, and a frame before impact has the club
# short of the ball.
STILL_DISK = 0.4  # of the ball's radius: the club touching the ball covers its edge, not this
# mean gray change inside that disk of a ball still on its spot. Still balls change 1 to 5, a
# ball on its way 8 or more.
STILL_MAX = 6.0
# ball radii looked at for the shaft: to the golfer's side, the far side, above, below the ball
SHAFT_AREA = (14.0, 6.0, 12.0, 2.0)
BALL_MASK = 1.6  # ball radii left out around the ball, which is light too
LIGHT_MAX_S, LIGHT_MIN_V = 70, 110  # HSV saturation under and value over: the shaft, not the mat
SHAFT_MIN = 5.0  # ball radii: the shortest segment that counts as the shaft
STEEP_DEG = 35.0  # the shaft comes down at least this steeply; the leading edge does not
NEAR_DIAMETERS = 4.0  # the shaft's line crosses the ball's row within this of the ball


@dataclass(frozen=True)
class Strike:
    contact: int  # index of the contact frame among the frames read
    contact_s: float
    offset_px: float | None  # ball center minus hosel, toward the toe; None without a shaft
    hosel: tuple[float, float] | None  # where the shaft's line crosses the ball's row, px
    ball: tuple[float, float, float]  # ball center x, y and radius, px


def contact_frame(
    frames: list[tuple[float, np.ndarray]], impact: int, reference: np.ndarray, rest: RestSpot
) -> int | None:
    """Of the impact frame (index `impact`) and the one after it, the last in which the ball is
    still on its spot; None when it is in neither. A ball is still while its middle looks as it
    did in `reference`, a frame before the downswing reached it. When the club covers the
    middle of the ball in both, there is no telling which is contact, and an earlier frame has
    the club short of the ball."""
    h, w = reference.shape[:2]
    yy, xx = np.ogrid[:h, :w]
    inside = (xx - rest.x * w) ** 2 + (yy - rest.y * h) ** 2 <= (STILL_DISK * rest.radius * h) ** 2
    before = _gray(reference)[inside]
    still = [
        i
        for i, (_, img) in enumerate(frames)
        if impact <= i <= impact + 1
        and float(np.abs(_gray(img)[inside] - before).mean()) <= STILL_MAX
    ]
    return still[-1] if still else None


def hosel_x(img: np.ndarray, rest: RestSpot, side: int) -> float | None:
    """Where the shaft's line crosses the ball's center row, in px, None when no shaft is seen.
    The shaft is the longest steep light segment near the ball whose line crosses that row
    within NEAR_DIAMETERS of it. `side` is 1 when the golfer stands left of the ball in the
    picture and -1 when right."""
    h, w = img.shape[:2]
    bx, by, r = rest.x * w, rest.y * h, rest.radius * h
    golfer, far, above, below = SHAFT_AREA
    xs = sorted((bx - side * golfer * r, bx + side * far * r))
    x0, x1 = max(0, int(xs[0])), min(w, int(xs[1]))
    y0, y1 = max(0, int(by - above * r)), min(h, int(by + below * r))
    hsv = cv2.cvtColor(np.ascontiguousarray(img[y0:y1, x0:x1]), cv2.COLOR_RGB2HSV)
    light = ((hsv[..., 1] < LIGHT_MAX_S) & (hsv[..., 2] > LIGHT_MIN_V)).astype(np.uint8) * 255
    cv2.circle(light, (round(bx - x0), round(by - y0)), round(BALL_MASK * r), 0, -1)
    lines = cv2.HoughLinesP(
        light,
        1,
        np.pi / 180,
        threshold=max(1, round(3 * r)),
        minLineLength=max(1, round(SHAFT_MIN * r)),
        maxLineGap=max(1, round(r / 2)),
    )
    best: tuple[float, float] | None = None  # (length, crossing)
    for xa, ya, xb, yb in lines.reshape(-1, 4).tolist() if lines is not None else []:
        if ya == yb or math.degrees(math.atan2(abs(ya - yb), abs(xa - xb))) < STEEP_DEG:
            continue
        cross = x0 + xa + (xb - xa) * (by - y0 - ya) / (yb - ya)
        if abs(cross - bx) > NEAR_DIAMETERS * 2 * r:
            continue
        length = math.hypot(xa - xb, ya - yb)
        if best is None or length > best[0]:
            best = (length, cross)
    return None if best is None else best[1]


def read_strike(
    frames: list[tuple[float, np.ndarray]],
    impact: int,
    reference: np.ndarray,
    rest: RestSpot,
    side: int,
) -> Strike | None:
    """The hosel's place in the contact frame. None without a contact frame; no offset when
    the shaft is not seen in it, because an earlier frame has the club short of the ball."""
    i = contact_frame(frames, impact, reference, rest)
    if i is None:
        return None
    t, img = frames[i]
    h, w = img.shape[:2]
    ball = (rest.x * w, rest.y * h, rest.radius * h)
    x = hosel_x(img, rest, side)
    offset = None if x is None else (ball[0] - x) * side
    return Strike(i, t, offset, None if x is None else (x, ball[1]), ball)


def session_radius(strikes: list[Strike | None]) -> float | None:
    """The median ball radius in px over the session's swings with a contact frame."""
    radii = [s.ball[2] for s in strikes if s is not None]
    return statistics.median(radii) if radii else None


def diameters(strike: Strike | None, radius: float | None) -> float | None:
    """The strike in ball diameters of `radius`, the session's ball radius."""
    if strike is None or strike.offset_px is None or not radius:
        return None
    return strike.offset_px / (2 * radius)


def write_strike_json(path: Path, strike: Strike | None, radius: float | None) -> None:
    data = {"strike": None, "contact_s": None, "hosel": None, "ball": None, "radius_px": None}
    if strike is not None:
        value = diameters(strike, radius)
        data = {
            "strike": None if value is None else round(value, 3),
            "contact_s": round(strike.contact_s, 4),
            "hosel": None if strike.hosel is None else [round(v, 1) for v in strike.hosel],
            "ball": [round(v, 1) for v in strike.ball],
            "radius_px": None if radius is None else round(radius, 2),
        }
    path.write_text(json.dumps(data) + "\n")


def _gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
