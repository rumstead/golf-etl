"""The simulator readout panel's place in a frame, shared by detection and the sim reader."""

from dataclasses import dataclass

Box = tuple[int, int, int, int]  # x0, x1, y0, y1

FIELDS = (
    "club_path",
    "carry_game",
    "face_to_path",
    "carry_raw",
    "back_spin",
    "total_length",
    "smash",
    "ball_speed",
    "peak_height",
    "total_spin",
    "face_to_target",
    "club_speed",
)  # the panel's cells as the simulator ships them: left to right, then top to bottom


@dataclass(frozen=True)
class Panel:
    box: Box
    score: float  # TM_CCOEFF_NORMED of the frame's edges against the reference, before settling
    # the field in each cell, left to right then top to bottom; None is a cell that is not read
    layout: tuple[str | None, ...] = FIELDS
