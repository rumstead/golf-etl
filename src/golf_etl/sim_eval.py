"""Sim readout accuracy against hand-read screen text, and glyph templates built from it.

Each labeled shot is read from LABEL_START_S after impact to the earliest of the next labeled
impact, MAX_AFTER_S after impact, and the end of the video, at SAMPLE_FPS. Each video is decoded
once per process: only the panel crop of each frame is kept.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from itertools import groupby
from pathlib import Path

import numpy as np
import yaml

from golf_etl.media import probe, read_frames
from golf_etl.sim import (
    FIELDS,
    SAMPLE_FPS,
    SUFFIX_LENGTH,
    Glyphs,
    Panel,
    build_glyphs,
    find_panel,
    pick_panel,
    read_shot,
    shot_window,
    value_boxes,
)

LABEL_START_S = 3.0  # after impact; the screen switches to the shot 0.2 to 0.5s after it


@dataclass(frozen=True)
class LabeledShot:
    video: str  # path as the labels file gives it
    impact_s: float
    labels: dict[str, str]  # screen text by field
    panel: Panel | None  # box within each crop, None when the frames agree on no panel
    crops: list[tuple[float, np.ndarray]]  # (time, panel crop) of every frame read


def labeled_cells(labels_path: Path) -> Iterator[tuple[str, np.ndarray, str, str]]:
    """(video, value cell RGB, field, screen text) for every labeled field in every frame read."""
    for shot in _shots(labels_path.resolve()):
        if shot.panel is None:
            continue
        boxes = dict(zip(FIELDS, value_boxes(shot.panel.box), strict=True))
        for _, crop in shot.crops:
            for field, text in shot.labels.items():
                x0, x1, y0, y1 = boxes[field]
                yield shot.video, crop[y0:y1, x0:x1], field, text


def score(
    labels_path: Path, glyphs_for: Callable[[str], Glyphs]
) -> tuple[int, int, int, list[str]]:
    """Right, wrong and blank labeled fields after voting each shot, and the lines to print.

    `glyphs_for(video)` gives the templates to read that video with. The lines are one per wrong
    or blank field, then a count per video and in total, with how many blanks are suffix fields.
    """
    total: Counter[str] = Counter()
    lines = []
    for video, shots in groupby(_shots(labels_path.resolve()), key=lambda s: s.video):
        counts: Counter[str] = Counter()
        glyphs = glyphs_for(video)
        for shot in shots:
            values = {}
            if shot.panel is not None:
                values = read_shot(shot.crops, shot.panel, glyphs).values
            for field, label in shot.labels.items():
                read = values.get(field)
                if read == label:
                    counts["right"] += 1
                    continue
                kind = "wrong" if read else "blank"
                counts[kind] += 1
                if kind == "blank" and field in SUFFIX_LENGTH:
                    counts["suffix"] += 1
                seen = read or ("no panel" if shot.panel is None else "nothing")
                lines.append(
                    f"{video} {shot.impact_s}s {field}: {kind}, read {seen}, label {label}"
                )
        lines.append(_count_line(video, counts))
        total.update(counts)
    lines.append(_count_line("total", total))
    return total["right"], total["wrong"], total["blank"], lines


def held_out(labels_path: Path) -> Callable[[str], Glyphs]:
    """Templates for each video built from the labeled cells of every other video."""

    @cache
    def glyphs_for(video: str) -> Glyphs:
        cells = labeled_cells(labels_path)
        return build_glyphs((cell, text) for v, cell, _, text in cells if v != video)

    return glyphs_for


def _count_line(name: str, counts: Counter[str]) -> str:
    return (
        f"{name}: right {counts['right']}, wrong {counts['wrong']}, blank {counts['blank']} "
        f"({counts['suffix']} suffix)"
    )


@cache
def _shots(labels_path: Path) -> tuple[LabeledShot, ...]:
    """Every labeled shot in file order, its frames read once and cut down to the panel."""
    shots = []
    for entry in yaml.safe_load(labels_path.read_text())["videos"]:
        path = labels_path.parent / entry["path"]
        info = probe(path)
        impacts = sorted(float(s["impact"]) for s in entry["shots"])
        for shot in entry["shots"]:
            impact = float(shot["impact"])
            later = [t for t in impacts if t > impact]
            start = impact + LABEL_START_S
            _, end = shot_window(impact, info.duration_s, later[0] if later else None, 0.0)
            frames = []
            if end > start:
                frames = list(read_frames(path, info, start, end, step_s=1 / SAMPLE_FPS))
            labels = {f: str(text) for f, text in shot.items() if f != "impact"}
            panel = pick_panel([find_panel(frame) for _, frame in frames])
            crops = []
            if panel is not None:
                x0, x1, y0, y1 = panel.box
                crops = [(t, frame[y0:y1, x0:x1].copy()) for t, frame in frames]
                panel = Panel((0, x1 - x0, 0, y1 - y0), panel.score)
            shots.append(LabeledShot(entry["path"], impact, labels, panel, crops))
    return tuple(shots)
