"""Synthetic swing clips for ball tracking: turf, a ball at rest, then the ball launched."""

import subprocess
from pathlib import Path

import numpy as np

from golf_etl.pose import (
    LEFT_ANKLE,
    LEFT_FOOT_INDEX,
    LEFT_HEEL,
    NUM_LANDMARKS,
    RIGHT_ANKLE,
    RIGHT_FOOT_INDEX,
    RIGHT_HEEL,
)

YELLOW = (235, 225, 40)
WHITE = (240, 240, 235)


def address_pose(
    ankle_y: float = 0.62, ankle_x: tuple[float, float] = (0.2, 0.3), toes: int = 1
) -> np.ndarray:
    """(33, 3) landmarks with only the feet set, normalized to the frame. Toes point right
    (`toes` 1), left (-1) or at the camera (0), about 60px from the heel at 960px tall."""
    lm = np.full((NUM_LANDMARKS, 3), np.nan)
    for ankle, heel, toe, x in (
        (LEFT_ANKLE, LEFT_HEEL, LEFT_FOOT_INDEX, ankle_x[0]),
        (RIGHT_ANKLE, RIGHT_HEEL, RIGHT_FOOT_INDEX, ankle_x[1]),
    ):
        lm[ankle] = (x, ankle_y, 0.9)
        if toes:
            lm[heel] = (x - 0.02 * toes, ankle_y + 0.02, 0.9)
            lm[toe] = (x + 0.09 * toes, ankle_y + 0.025, 0.9)
        else:
            lm[heel] = (x, ankle_y + 0.01, 0.9)
            lm[toe] = (x + 0.01, ankle_y + 0.07, 0.9)
    return lm


def make_ball_clip(
    path: Path,
    *,
    rest: tuple[float, float] | None = (360, 620),
    launch_frame: int = 300,
    velocity: tuple[float, float] = (-4.0, -20.0),
    color: tuple[int, int, int] = YELLOW,
    radius: int = 5,
    size: tuple[int, int] = (540, 960),
    fps: int = 120,
    frames: int = 480,
    shift_from: int | None = None,
    decoy: tuple[int, int, int] | None = None,
) -> Path:
    """Write an H.264 clip of a ball resting at `rest` (pixels) that flies off at
    `velocity` (pixels per frame) from `launch_frame`. No ball when `rest` is None.
    From `shift_from` on the picture drops a quarter frame, like a bumped camera. A `decoy`
    (x, y, radius) is a white patch that vanishes at launch, like a shoe turning through."""
    w, h = size
    rng = np.random.default_rng(7)
    turf = np.zeros((h, w, 3), np.int16)
    turf[...] = (70, 120, 50)
    turf[: h // 3] = (200, 210, 225)  # sky
    turf += rng.integers(-12, 12, (h, w, 1), dtype=np.int16)
    tilted = np.roll(turf, h // 4, axis=0)
    noisy = [
        np.clip(scene + rng.integers(-3, 3, (h, w, 1), dtype=np.int16), 0, 255).astype(np.uint8)
        for scene in (turf, tilted)
        for _ in range(4)
    ]
    dy, dx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24"]
    cmd += ["-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    with subprocess.Popen([*cmd, str(path)], stdin=subprocess.PIPE) as proc:
        assert proc.stdin is not None
        for k in range(frames):
            scene = 4 if shift_from is not None and k >= shift_from else 0
            img = noisy[scene + k % 4].copy()
            if decoy is not None and k < launch_frame:
                cx, cy, r = decoy
                img[cy - r : cy + r, cx - r : cx + r] = WHITE
            if rest is not None:
                dt = max(0, k - launch_frame)
                bx = round(rest[0] + velocity[0] * dt)
                by = round(rest[1] + velocity[1] * dt)
                ys, xs = by + dy, bx + dx
                inside = (dx**2 + dy**2 <= radius**2) & (ys >= 0) & (ys < h) & (xs >= 0) & (xs < w)
                img[ys[inside], xs[inside]] = color
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed writing the synthetic clip")
    return path
