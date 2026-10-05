"""Turning RGB frames into the labeled JPEGs Claude reads."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from golf_etl.pose import LEFT_ANKLE, RIGHT_ANKLE

# Shoulders, arms, torso, legs. Face and hand landmarks only add clutter.
SKELETON = [
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (24, 26),
    (26, 28),
    (27, 31),
    (28, 32),
]
ZOOM_BAND = 0.25  # impact zoom band height as a fraction of frame height


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


def draw_pose(rgb: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    if np.isnan(landmarks).all():
        return out
    h, w = out.shape[:2]
    pts = {i: (int(x * w), int(y * h)) for i, (x, y, _) in enumerate(landmarks) if not np.isnan(x)}
    thickness = max(2, w // 400)
    for a, b in SKELETON:
        if a in pts and b in pts:
            cv2.line(out, pts[a], pts[b], (0, 255, 0), thickness, cv2.LINE_AA)
    for i in {i for pair in SKELETON for i in pair} & pts.keys():
        cv2.circle(out, pts[i], thickness * 2, (255, 64, 64), -1, cv2.LINE_AA)
    return out


def grid(
    images: list[np.ndarray], labels: list[str], cols: int, long_edge: int, max_pixels: int
) -> np.ndarray:
    """Sheet in reading order, sized to fit long_edge and max_pixels, labeled after resizing."""
    h, w = images[0].shape[:2]
    rows = -(-len(images) // cols)
    scale = min(long_edge / max(cols * w, rows * h), (max_pixels / (cols * w * rows * h)) ** 0.5)
    cell_w, cell_h = int(w * scale), int(h * scale)
    cells = [
        label(cv2.resize(img, (cell_w, cell_h), interpolation=cv2.INTER_AREA), text)
        for img, text in zip(images, labels, strict=True)
    ]
    cells += [np.zeros((cell_h, cell_w, 3), np.uint8)] * (rows * cols - len(cells))
    return np.vstack([np.hstack(cells[r * cols : (r + 1) * cols]) for r in range(rows)])


def zoom_crop(rgb: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    """Native-resolution band at ball height (ankle level).

    The ball sits between the feet face-on but out past the toes down the line, so the band
    spans the frame width (up to 1.2 frame heights) instead of guessing where the ball is.
    """
    h, w = rgb.shape[:2]
    band_h = round(h * ZOOM_BAND)
    band_w = min(w, round(h * 1.2))
    ankles = landmarks[[LEFT_ANKLE, RIGHT_ANKLE], :2]
    if np.isnan(ankles).any():
        cx, cy = w / 2, h * 0.8
    else:
        cx, cy = ankles[:, 0].mean() * w, ankles[:, 1].mean() * h
    x0 = int(np.clip(cx - band_w / 2, 0, w - band_w))
    y0 = int(np.clip(cy - band_h / 2, 0, h - band_h))
    return rgb[y0 : y0 + band_h, x0 : x0 + band_w]


def save_jpeg(rgb: np.ndarray, path: Path, quality: int) -> None:
    """sRGB JPEG with no EXIF or other metadata."""
    Image.fromarray(rgb).save(path, "JPEG", quality=quality, optimize=True)
