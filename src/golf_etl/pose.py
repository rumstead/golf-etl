"""Pose tracking with MediaPipe Pose Landmarker, reduced to what swing analysis needs."""

from collections.abc import Iterable
from dataclasses import dataclass

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_ANKLE, RIGHT_ANKLE = 27, 28
NUM_LANDMARKS = 33


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
