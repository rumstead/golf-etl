"""Pose tracking with MediaPipe Pose Landmarker, reduced to what swing analysis needs."""

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_ANKLE, RIGHT_ANKLE = 27, 28
LEFT_HEEL, RIGHT_HEEL = 29, 30
LEFT_FOOT_INDEX, RIGHT_FOOT_INDEX = 31, 32
NUM_LANDMARKS = 33

# MediaPipe landmark index of each joint written to pose.csv
CSV_JOINTS = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_wrist": LEFT_WRIST,
    "right_wrist": RIGHT_WRIST,
    "left_hip": LEFT_HIP,
    "right_hip": RIGHT_HIP,
    "left_knee": 25,
    "right_knee": 26,
    "left_heel": 29,
    "right_heel": 30,
}


def leg_height(landmarks: np.ndarray) -> np.ndarray:
    """Hip to ankle height in frame heights for landmarks shaped (..., 33, 3), NaN where the
    hips or ankles are missing."""
    y = landmarks[..., 1]
    ankles = (y[..., LEFT_ANKLE] + y[..., RIGHT_ANKLE]) / 2
    return ankles - (y[..., LEFT_HIP] + y[..., RIGHT_HIP]) / 2


def toes_direction(landmarks: np.ndarray, aspect: float) -> int:
    """1 or -1 when the toes point right or left in the picture, 0 when they point at the
    camera (face-on) or no foot is seen. `aspect` is frame width over height."""
    steps = [
        (landmarks[toe, :2] - landmarks[heel, :2]) * (aspect, 1.0)
        for heel, toe in ((LEFT_HEEL, LEFT_FOOT_INDEX), (RIGHT_HEEL, RIGHT_FOOT_INDEX))
    ]
    steps = [step for step in steps if not np.isnan(step).any()]
    if not steps:
        return 0
    length = float(np.median([np.hypot(*step) for step in steps]))
    dx = float(np.mean([step[0] for step in steps]))
    return int(np.sign(dx)) if abs(dx) > 0.5 * length else 0


@dataclass(frozen=True)
class PoseTrack:
    times: np.ndarray  # (n,) seconds
    landmarks: np.ndarray  # (n, 33, 3): x, y normalized to the frame, visibility; NaN when no pose
    aspect: float  # frame width / height

    def nearest(self, t: float) -> np.ndarray:
        """Landmarks (33, 3) of the sample closest to t."""
        return self.landmarks[int(np.argmin(np.abs(self.times - t)))]

    def hands(self) -> np.ndarray:
        """(n, 2) midpoint of both wrists, x scaled so both axes are in frame heights."""
        mid = (self.landmarks[:, LEFT_WRIST, :2] + self.landmarks[:, RIGHT_WRIST, :2]) / 2
        return mid * np.array([self.aspect, 1.0])

    def hand_speed(self) -> np.ndarray:
        """(n,) hand speed in frame heights per second, lightly smoothed; NaN without a pose."""
        if len(self.times) < 2:
            return np.full(len(self.times), np.nan)
        pos = self.hands()
        vel = np.gradient(pos, self.times, axis=0)
        speed = np.linalg.norm(vel, axis=1)
        kernel = np.ones(3) / 3
        padded = np.pad(speed, 1, mode="edge")
        return np.convolve(padded, kernel, mode="valid")

    def hand_height(self) -> np.ndarray:
        """(n,) hand height, larger is higher (image y grows downward)."""
        return -self.hands()[:, 1]

    def hands_below_hips(self) -> np.ndarray:
        """(n,) how far the hands sit below the hips, in frame heights; negative is above."""
        hips = (self.landmarks[:, LEFT_HIP, 1] + self.landmarks[:, RIGHT_HIP, 1]) / 2
        return self.hands()[:, 1] - hips

    def hands_below_shoulders(self) -> np.ndarray:
        """(n,) how far the hands sit below the shoulders, in frame heights; negative is above."""
        shoulders = (self.landmarks[:, LEFT_SHOULDER, 1] + self.landmarks[:, RIGHT_SHOULDER, 1]) / 2
        return self.hands()[:, 1] - shoulders

    def leg_length(self) -> float:
        """Median hip to ankle height in frame heights, NaN when the legs are never seen. It
        scales with how big the golfer is in frame, so speeds divided by it do not depend on
        where the phone is."""
        legs = leg_height(self.landmarks)
        if np.all(np.isnan(legs)):
            return float("nan")
        return float(np.nanmedian(legs))


def write_pose_csv(
    track: PoseTrack, path: Path, impact_s: float, clip_start_s: float, clip_fps: float
) -> None:
    """One row per pose sample: video time, ms from impact, the nearest clip.mp4 frame, then
    x, y (fractions of the frame, top left is 0, 0) and visibility per joint. A sample with
    no pose leaves its joint cells empty."""
    header = ["time_s", "t_ms", "clip_frame"]
    header += [f"{name}_{axis}" for name in CSV_JOINTS for axis in ("x", "y", "vis")]
    idx = list(CSV_JOINTS.values())
    with path.open("w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(header)
        for t, lm in zip(track.times, track.landmarks, strict=True):
            row = [
                f"{t:.3f}",
                round((t - impact_s) * 1000),
                max(0, round((t - clip_start_s) * clip_fps)),
            ]
            row += ["" if np.isnan(v) else f"{v:.3f}" for v in lm[idx].ravel()]
            out.writerow(row)


class PoseEstimator:
    def __init__(self, model_path: str):
        self.model_path = model_path

    def track(self, frames: Iterable[tuple[float, np.ndarray]]) -> PoseTrack:
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=2,
        )
        times: list[float] = []
        rows: list[np.ndarray] = []
        aspect = 1.0
        with vision.PoseLandmarker.create_from_options(options) as landmarker:
            for t, rgb in frames:
                aspect = rgb.shape[1] / rgb.shape[0]
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
                result = landmarker.detect_for_video(image, int(round(t * 1000)))
                times.append(t)
                rows.append(_largest_pose(result.pose_landmarks))
        landmarks = np.stack(rows) if rows else np.empty((0, NUM_LANDMARKS, 3))
        return PoseTrack(np.asarray(times), landmarks, aspect)


def _largest_pose(poses) -> np.ndarray:
    """The golfer is the biggest person in frame; neighbors in other bays are smaller."""
    if not poses:
        return np.full((NUM_LANDMARKS, 3), np.nan)
    arrays = [np.array([[p.x, p.y, p.visibility or 0.0] for p in pose]) for pose in poses]

    def area(a: np.ndarray) -> float:
        return float(np.ptp(a[:, 0]) * np.ptp(a[:, 1]))

    return max(arrays, key=area)
