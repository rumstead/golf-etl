"""Runtime settings. Every field can be overridden with GOLF_<FIELD> in the environment."""

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # detection
    onset_highpass_hz: float = 2000.0
    onset_delta: float = 0.2
    onset_min_gap_s: float = 8.0
    impact_audio_lag_ms: float = 12.0
    confirm_window_ms: int = 300
    confirm_min_speed: float = 1.0
    short_clip_s: float = 15.0
    pose_long_edge: int = 640
    pose_max_fps: float = 60.0
    pose_model_path: str = "/opt/golf-etl/pose_landmarker_lite.task"
    # slicing and frames
    pre_impact_s: float = 2.5
    post_impact_s: float = 1.5
    after_shot_s: float = 8.0  # simulators show the shot's final numbers by about 8s
    still_speed: float = 0.15
    sharpness_radius: int = 2
    frame_long_edge: int = 2560
    frame_max_pixels: int = 3_700_000  # Claude reads up to about 3.75MP
    jpeg_quality: int = 90
    clip_long_edge: int = 1920
    clip_crf: int = 23
    # drive
    remote: str = "gdrive:golf"  # rclone remote and folder; a local path works too
    max_attempts: int = 3
    failed_ttl_days: int = 3
    clip_ttl_days: int = 14
    session_ttl_days: int = 60
    max_bytes: int = 3 * 1024**3
    scratch_dir: str = "/scratch"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        overrides = {}
        for field in dataclasses.fields(cls):
            key = f"GOLF_{field.name.upper()}"
            if key not in env:
                continue
            caster = type(field.default)
            try:
                overrides[field.name] = caster(env[key])
            except ValueError as exc:
                raise ValueError(f"{key}={env[key]!r} is not a valid {caster.__name__}") from exc
        return cls(**overrides)
