"""Which field is in which cell of the readout panel, told from the numbers.

The simulator lets its tiles be moved, and the titles under the numbers are too small in a
phone video to read. The numbers say what they are instead: only a club path ends in I-O or
O-I, only a face ends in C or O, only smash has two decimals, only spin has none, and the
rest are tied together by what the simulator computes (see `broken`)."""

from collections import Counter
from itertools import permutations

import numpy as np

from golf_etl.panel import FIELDS, Box
from golf_etl.sim import Glyphs, read_cell, signed_value, value_boxes

# How a cell's number is written, and the fields written that way. A cell is read as the
# first field of each kind.
KINDS = {
    "path": ("club_path",),
    "face": ("face_to_path", "face_to_target"),
    "smash": ("smash",),
    "spin": ("back_spin", "total_spin"),
    "decimal": (
        "club_speed",
        "ball_speed",
        "carry_game",
        "carry_raw",
        "total_length",
        "peak_height",
    ),
}
# The worst seen on 94 published and labeled shots is 0.006, 0.1 and 5.6 of 96.5 yards.
SMASH_TOLERANCE = 0.011  # smash against ball speed over club speed
FACE_TOLERANCE = 0.16  # face to target against club path plus face to path, in degrees
CARRY_TOLERANCE = (0.2, 1.0)  # carry (game) against carry (raw): a share of it plus yards
SPIN_SHARE = 0.8  # a spin cell also reads as a decimal with its point lost, about as often
MIN_KIND_READS = 2  # frames a cell must read in as its kind
MIN_HELD = 2  # frames a layout must hold in
MAX_BROKEN = 0.25  # of the frames it holds in, how many it may break in (a misread)
MIN_CHECKS = 2  # of the checks in `broken` a frame must be able to run to count either way

Texts = list[dict[str, str | None]]  # per cell, the text read as each kind
_KIND_OF = {field: kind for kind, fields in KINDS.items() for field in fields}


def cell_texts(frame: np.ndarray, box: Box, glyphs: Glyphs) -> Texts:
    """Every cell of the panel at `box` read as each kind of number."""
    out = []
    for x0, x1, y0, y1 in value_boxes(box):
        cell = frame[y0:y1, x0:x1]
        out.append({kind: read_cell(cell, fields[0], glyphs) for kind, fields in KINDS.items()})
    return out


def broken(values: dict[str, str | None]) -> tuple[set[str], int]:
    """The fields of every check the values fail, and how many checks could run.

    Smash is ball speed over club speed. Face to target is club path plus face to path.
    Total spin is at least back spin. Carry (game) is near carry (raw), and total length is
    at least carry (game). A check runs only when all its fields were read."""
    v = {field: signed_value(field, text) for field, text in values.items()}
    checks = [
        (
            ("smash", "ball_speed", "club_speed"),
            lambda s, b, c: c > 0 and abs(b / c - s) <= SMASH_TOLERANCE,
        ),
        (
            ("face_to_target", "club_path", "face_to_path"),
            lambda t, p, f: abs(p + f - t) <= FACE_TOLERANCE,
        ),
        (("total_spin", "back_spin"), lambda t, b: t >= b),
        (
            ("carry_game", "carry_raw"),
            lambda g, r: abs(g - r) <= CARRY_TOLERANCE[0] * r + CARRY_TOLERANCE[1],
        ),
        (("total_length", "carry_game"), lambda t, g: t >= g),
    ]
    failed: set[str] = set()
    ran = 0
    for fields, holds in checks:
        numbers = [v.get(f) for f in fields]
        if None in numbers:
            continue
        ran += 1
        if not holds(*numbers):
            failed.update(fields)
    return failed, ran


def solve_layout(samples: list[Texts]) -> tuple[str | None, ...] | None:
    """The field in each cell, from frames of `cell_texts`, or None when the numbers do not
    settle it.

    Each cell's kind is the one it reads as in the most frames. The panel must have one path,
    two faces, one smash, two spins and six decimals. Every way of handing the fields of a
    kind to its cells is then tried against `broken` in every frame, and the one that holds
    in the most frames wins when it holds in MIN_HELD and no other does as well. Carry (game)
    and carry (raw) show nearly the same number, so they keep the order the simulator ships
    them in. Ties go to the layout nearest the shipped one."""
    if not samples:
        return None
    kinds = [_kind([sample[i] for sample in samples]) for i in range(len(samples[0]))]
    if Counter(kinds) != Counter({kind: len(fields) for kind, fields in KINDS.items()}):
        return None
    cells = {kind: [i for i, k in enumerate(kinds) if k == kind] for kind in KINDS}
    scored = []
    for faces in permutations(KINDS["face"]):
        for spins in permutations(KINDS["spin"]):
            for decimals in permutations(KINDS["decimal"]):
                layout = _layout(cells, faces, spins, decimals)
                if layout.index("carry_game") > layout.index("carry_raw"):
                    continue
                held = broke = 0
                for sample in samples:
                    values = {field: sample[i][kinds[i]] for i, field in enumerate(layout)}
                    failed, ran = broken(values)
                    if ran >= MIN_CHECKS:
                        held += not failed
                        broke += bool(failed)
                if held >= MIN_HELD and broke <= MAX_BROKEN * held:
                    shipped = sum(a == b for a, b in zip(layout, FIELDS, strict=True))
                    scored.append((held - broke, shipped, layout))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None
    return scored[0][2]


def shipped_holds(samples: list[Texts]) -> bool:
    """Whether the layout the simulator ships holds in at least as many frames as it breaks
    in, so it can stand in for a layout the numbers did not settle."""
    held = broke = 0
    for sample in samples:
        values = {f: texts[_KIND_OF[f]] for f, texts in zip(FIELDS, sample, strict=True)}
        failed, ran = broken(values)
        if ran >= MIN_CHECKS:
            held += not failed
            broke += bool(failed)
    return held >= broke


def _layout(cells: dict[str, list[int]], faces, spins, decimals) -> tuple[str, ...]:
    layout: list[str] = [""] * sum(len(c) for c in cells.values())
    fields = {"path": KINDS["path"], "smash": KINDS["smash"]}
    fields |= {"face": faces, "spin": spins, "decimal": decimals}
    for kind, where in cells.items():
        for i, field in zip(where, fields[kind], strict=True):
            layout[i] = field
    return tuple(layout)


def _kind(texts: list[dict[str, str | None]]) -> str | None:
    """The kind of number a cell shows: the kind it reads as in the most frames, with spin
    over decimal when it reads as both. None when it reads as nothing often enough."""
    counts = {kind: sum(t[kind] is not None for t in texts) for kind in KINDS}
    if counts["spin"] >= MIN_KIND_READS and counts["spin"] >= SPIN_SHARE * counts["decimal"]:
        counts["decimal"] = 0
    kind = max(counts, key=lambda k: counts[k])
    return kind if counts[kind] >= MIN_KIND_READS else None
