"""Turning RGB frames into the labeled JPEGs Claude reads."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from golf_etl.pose import (
    LEFT_ANKLE,
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_ANKLE,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    leg_height,
    toes_direction,
)

CROP_JOINTS = [
    LEFT_SHOULDER,
    RIGHT_SHOULDER,
    LEFT_WRIST,
    RIGHT_WRIST,
    LEFT_HIP,
    RIGHT_HIP,
    LEFT_ANKLE,
    RIGHT_ANKLE,
]
CROP_HEAD = 0.5  # leg lengths kept over the shoulders for the head
CROP_ABOVE = 0.9  # leg lengths kept over the hands for the club at the top
CROP_BELOW = 0.2  # leg lengths kept under the ankles and the ball
CROP_SIDE = 0.6  # leg lengths kept to each side of the golfer down the line
CROP_SIDE_FACE_ON = 1.2  # leg lengths kept to each side of the hands face-on, a club length
CROP_TO_BALL = 1.3  # leg lengths kept on the toe side down the line without a rest spot
CROP_BALL = 0.3  # leg lengths kept to each side of the ball
ZOOM_BAND = 0.25  # impact zoom band height as a fraction of frame height
ZOOM_FACE_ON_DROP = 0.35  # face-on the band centers this many leg lengths below the ankles


def fit(rgb: np.ndarray, long_edge: int, max_pixels: int | None = None) -> np.ndarray:
    """Downscale to fit long_edge (and max_pixels when given). Never upscales."""
    h, w = rgb.shape[:2]
    scale = long_edge / max(h, w)
    if max_pixels:
        scale = min(scale, (max_pixels / (w * h)) ** 0.5)
    if scale >= 1:
        return rgb
    size = (int(w * scale), int(h * scale))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)


def label(rgb: np.ndarray, text: str) -> np.ndarray:
    out = rgb.copy()
    h, w = out.shape[:2]
    scale = max(0.5, w / 1600)
    thickness = max(1, round(scale * 2))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    pad = round(10 * scale)
    cv2.rectangle(out, (0, 0), (tw + 2 * pad, th + base + 2 * pad), (0, 0, 0), -1)
    cv2.putText(
        out,
        text,
        (pad, pad + th),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return out


def golfer_crop(
    landmarks: np.ndarray,
    shape: tuple[int, ...],
    ball: tuple[float, float] | None = None,
) -> tuple[int, int, int, int]:
    """The box (x0, x1, y0, y1) in px around the golfer, the club and the ball over a swing,
    for landmarks shaped (n, 33, 3) and the ball's rest spot in fractions of the frame.

    The box starts from the shoulders, hands, hips and ankles, and room is left around them in
    leg lengths: CROP_HEAD over the shoulders, CROP_ABOVE over the hands for the club at the
    top, CROP_BELOW under the ankles for the feet, and to the sides CROP_SIDE down the line,
    where the club swings toward and away from the phone, or CROP_SIDE_FACE_ON beyond the hands
    face-on, where it swings across the picture. Down the line the box reaches the ball, or
    CROP_TO_BALL past the golfer on the toe side when the rest spot is not known. The whole
    frame without a pose.
    """
    h, w = shape[:2]
    leg = leg_height(landmarks)
    if not landmarks.size or np.isnan(leg).all():
        return 0, w, 0, h
    leg_px = float(np.nanmedian(leg)) * h
    xs, ys = landmarks[:, :, 0] * w, landmarks[:, :, 1] * h
    x0, x1 = float(np.nanmin(xs[:, CROP_JOINTS])), float(np.nanmax(xs[:, CROP_JOINTS]))
    y1 = float(np.nanmax(ys[:, CROP_JOINTS])) + CROP_BELOW * leg_px
    y0 = min(
        float(np.nanmin(ys[:, [LEFT_SHOULDER, RIGHT_SHOULDER]])) - CROP_HEAD * leg_px,
        float(np.nanmin(ys[:, [LEFT_WRIST, RIGHT_WRIST]])) - CROP_ABOVE * leg_px,
    )
    toes = toes_direction(np.nanmedian(landmarks, axis=0), w / h)
    if toes:
        x0, x1 = x0 - CROP_SIDE * leg_px, x1 + CROP_SIDE * leg_px
        if ball is None and toes > 0:
            x1 += (CROP_TO_BALL - CROP_SIDE) * leg_px
        elif ball is None:
            x0 -= (CROP_TO_BALL - CROP_SIDE) * leg_px
    else:
        hands = xs[:, [LEFT_WRIST, RIGHT_WRIST]]
        x0 = min(x0, float(np.nanmin(hands)) - CROP_SIDE_FACE_ON * leg_px)
        x1 = max(x1, float(np.nanmax(hands)) + CROP_SIDE_FACE_ON * leg_px)
    if ball is not None:
        x0 = min(x0, ball[0] * w - CROP_BALL * leg_px)
        x1 = max(x1, ball[0] * w + CROP_BALL * leg_px)
        y1 = max(y1, ball[1] * h + CROP_BELOW * leg_px)
    x0, x1 = int(np.clip(x0, 0, w - 1)), int(np.clip(x1, 1, w))
    y0, y1 = int(np.clip(y0, 0, h - 1)), int(np.clip(y1, 1, h))
    return x0, max(x1, x0 + 1), y0, max(y1, y0 + 1)


def grid(
    images: list[np.ndarray], labels: list[str], cols: int, long_edge: int, max_pixels: int
) -> np.ndarray:
    """Sheet in reading order, sized to fit long_edge and max_pixels and never upscaled,
    labeled after resizing."""
    h, w = images[0].shape[:2]
    rows = -(-len(images) // cols)
    scale = min(long_edge / max(cols * w, rows * h), (max_pixels / (cols * w * rows * h)) ** 0.5)
    scale = min(scale, 1.0)
    cell_w, cell_h = int(w * scale), int(h * scale)
    cells = [
        label(cv2.resize(img, (cell_w, cell_h), interpolation=cv2.INTER_AREA), text)
        for img, text in zip(images, labels, strict=True)
    ]
    cells += [np.zeros((cell_h, cell_w, 3), np.uint8)] * (rows * cols - len(cells))
    return np.vstack([np.hstack(cells[r * cols : (r + 1) * cols]) for r in range(rows)])


def zoom_crop(rgb: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    """Native-resolution band at ball height: centered on the ankles down the line, and
    ZOOM_FACE_ON_DROP leg lengths below them face-on, where the ball sits between the golfer
    and the phone, about half a leg below the ankles in the picture.

    Sideways the ball sits between the feet face-on but out past the toes down the line, so
    the band spans the frame width (up to 1.2 frame heights) instead of guessing.
    """
    h, w = rgb.shape[:2]
    band_h = round(h * ZOOM_BAND)
    band_w = min(w, round(h * 1.2))
    ankles = landmarks[[LEFT_ANKLE, RIGHT_ANKLE], :2]
    if np.isnan(ankles).any():
        cx, cy = w / 2, h * 0.8
    else:
        cx, cy = ankles[:, 0].mean() * w, ankles[:, 1].mean() * h
        leg = float(leg_height(landmarks))
        if toes_direction(landmarks, w / h) == 0 and leg > 0:
            cy += ZOOM_FACE_ON_DROP * leg * h
    x0 = int(np.clip(cx - band_w / 2, 0, w - band_w))
    y0 = int(np.clip(cy - band_h / 2, 0, h - band_h))
    return rgb[y0 : y0 + band_h, x0 : x0 + band_w]


def save_jpeg(rgb: np.ndarray, path: Path, quality: int) -> None:
    """sRGB JPEG with no EXIF or other metadata."""
    Image.fromarray(rgb).save(path, "JPEG", quality=quality, optimize=True)
