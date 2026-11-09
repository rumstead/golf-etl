"""The simulator readout panel's place in a frame, shared by detection and the sim reader."""

from dataclasses import dataclass

Box = tuple[int, int, int, int]  # x0, x1, y0, y1


@dataclass(frozen=True)
class Panel:
    box: Box
    score: float  # TM_CCOEFF_NORMED of the frame's edges against the reference, before settling
