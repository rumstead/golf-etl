"""The simulator's second panel, on the left of the screen: side spin, the launch angles and
offline.

It repeats six of the readout panel's numbers, which is how it is found (each number's picture
in the readout panel is looked for on the left) and how a frame is trusted: its left panel
counts only when the numbers it shares with the readout panel read the same there."""

import math
from itertools import product

import cv2
import numpy as np

from golf_etl.panel import Box, Panel
from golf_etl.sim import FIELDS, Glyphs, read_cell, value_boxes, vote

# The left panel's cells, left to right then top to bottom. None is a cell that is not read.
GRID = (
    "side_spin",
    "club_speed",
    "back_spin",
    "club_path",
    "vla",
    "face_to_target",
    "hla",
    "face_to_path",
    "offline",
    None,
    "peak_height",
    None,
)
LEFT_FIELDS = ("side_spin", "vla", "hla", "offline")  # what only the left panel shows
SHARED = tuple(f for f in GRID if f in FIELDS)  # what both panels show
ANCHOR = "back_spin"  # the shared number in the left fields' column
COLUMNS, ROWS = 2, 6
VALUE_CENTER = 0.38  # of a cell's height, where the middle of its value sits
SCALES = np.round(np.arange(0.8, 1.16, 0.05), 2)  # of the readout panel's number size
MIN_MATCH = 0.5  # TM_CCOEFF_NORMED a number's picture needs on the left
SEARCH_ROWS = 0.5  # of the readout panel's height searched above and below its rows
CELL_SIZE = (0.6, 1.4)  # of the readout panel's cell size a left cell can be
FIT_PX = 0.2  # of a cell: how near a number must sit to where a grid puts it to back the grid
MIN_SHARED = 3  # shared numbers that must read the same to trust a found box
MIN_AGREE = 2  # shared numbers that must read, all the same, for a frame's left panel to count
BOX_AGREE_PX = 4  # boxes this close on every edge are the same panel
MIN_BOXES = 2  # agreeing boxes needed to trust a left panel
SETTLE_PX = 6  # how far each edge of the found box may move as it settles


def left_reads(frame: np.ndarray, box: Box, glyphs: Glyphs) -> dict[str, str | None]:
    """Every read cell of the left panel at `box` in an RGB frame, by field."""
    cells = zip(GRID, value_boxes(box), strict=True)
    return {
        field: read_cell(frame[y0:y1, x0:x1], field, glyphs)
        for field, (x0, x1, y0, y1) in cells
        if field is not None
    }


def agrees(reads: dict[str, str | None], values: dict[str, str | None], least: int) -> bool:
    """Whether at least `least` of the numbers both panels show read on the left, every one
    the same as the readout panel's `values`."""
    both = [(reads.get(f), values.get(f)) for f in SHARED]
    both = [(left, right) for left, right in both if left is not None and right is not None]
    return len(both) >= least and all(left == right for left, right in both)


def find_left_panel(
    frame: np.ndarray, panel: Panel, values: dict[str, str | None], glyphs: Glyphs
) -> Box | None:
    """The left panel's box in an RGB frame, given the readout panel and what it reads in this
    frame, or None when it is not found.

    Each shared number's value cell in the readout panel is matched against the frame left of
    it at SCALES. The hits vote for a grid: every pair of hits in different columns sets the
    columns, every pair in different rows sets the rows, and the grid the most hits sit on is
    kept. It is the left panel only when MIN_SHARED shared numbers read the same through it."""
    x0, x1, y0, y1 = panel.box
    cell_w, cell_h = (x1 - x0) / COLUMNS, (y1 - y0) / ROWS
    top = max(0, int(y0 - SEARCH_ROWS * (y1 - y0)))
    bottom = min(frame.shape[0], int(y1 + SEARCH_ROWS * (y1 - y0)))
    area = cv2.cvtColor(frame[top:bottom, : max(0, x0 - int(cell_w))], cv2.COLOR_RGB2GRAY)
    cells = dict(zip(FIELDS, value_boxes(panel.box), strict=True))
    hits = []  # (column, row, x, y) of a shared number's middle on the left
    for field in SHARED:
        if values.get(field) is None:
            continue
        cx0, cx1, cy0, cy1 = cells[field]
        hit = _best_match(area, cv2.cvtColor(frame[cy0:cy1, cx0:cx1], cv2.COLOR_RGB2GRAY))
        if hit is not None:
            row, column = divmod(GRID.index(field), COLUMNS)
            hits.append((column, row, hit[0], hit[1] + top))
    best, backing = None, 0
    for a, b in product(hits, repeat=2):
        if a[0] != 0 or b[0] != 1:
            continue
        width = b[2] - a[2]
        if not CELL_SIZE[0] * cell_w <= width <= CELL_SIZE[1] * cell_w:
            continue
        for c, d in product(hits, repeat=2):
            if d[1] <= c[1]:
                continue
            height = (d[3] - c[3]) / (d[1] - c[1])
            if not CELL_SIZE[0] * cell_h <= height <= CELL_SIZE[1] * cell_h:
                continue
            left, up = a[2] - width / 2, c[3] - (c[1] + VALUE_CENTER) * height
            on = sum(
                abs(left + (col + 0.5) * width - x) <= FIT_PX * width
                and abs(up + (row + VALUE_CENTER) * height - y) <= FIT_PX * height
                for col, row, x, y in hits
            )
            if on > backing:
                backing = on
                best = (left, left + COLUMNS * width, up, up + ROWS * height)
    if best is None or backing < MIN_SHARED:
        return None
    box = tuple(round(v) for v in best)
    return box if agrees(left_reads(frame, box, glyphs), values, MIN_SHARED) else None


def pick_left_panel(boxes: list[Box]) -> Box | None:
    """The box most found boxes agree on within BOX_AGREE_PX, as the median of those that
    agree, or None when fewer than MIN_BOXES do."""

    def near(a: Box, b: Box) -> bool:
        return all(abs(x - y) <= BOX_AGREE_PX for x, y in zip(a, b, strict=True))

    groups = [[b for b in boxes if near(a, b)] for a in boxes]
    if not groups:
        return None
    group = max(groups, key=len)
    if len(group) < MIN_BOXES:
        return None
    return tuple(int(v) for v in np.median(np.array(group), axis=0).round())


def settle_left_panel(
    samples: list[tuple[np.ndarray, dict[str, str | None]]], box: Box, glyphs: Glyphs
) -> Box:
    """`box` with each edge moved up to SETTLE_PX to where the most cells read, over frames
    that show the left panel and what the readout panel reads in each.

    A box fitted from where the numbers sit lines up the middle rows, and a few px off at
    the top or bottom row cuts into its numbers. An edge goes to the middle of the offsets
    that read the most cells, twice around, and never to where a shared number reads
    something else."""

    def cells_read(candidate: Box) -> int:
        count = 0
        for frame, values in samples:
            reads = left_reads(frame, candidate, glyphs)
            if not agrees(reads, values, MIN_AGREE):
                return -1
            count += sum(v is not None for v in reads.values())
        return count

    best = list(box)
    for _ in range(2):
        for edge in range(4):
            scores = {}
            for move in range(-SETTLE_PX, SETTLE_PX + 1):
                candidate = best.copy()
                candidate[edge] += move
                scores[move] = cells_read(tuple(candidate))
            top = max(scores.values())
            if top > 0:
                tied = [move for move, score in scores.items() if score == top]
                best[edge] += tied[len(tied) // 2]
    return tuple(best)


def read_left(
    reads: list[dict[str, str | None]], values: dict[str, str | None]
) -> tuple[dict[str, str | None], dict[str, int]]:
    """The left panel's own fields and their votes, from each frame's `left_reads` and the
    shot's readout panel `values`.

    A frame counts when MIN_AGREE of the numbers both panels show read in it, all the same as
    the shot's: the left panel is then in view, in place, and showing this shot. One of them
    must be back spin, which sits in the column the left panel's own fields are in and fills
    its cell, so the column's left edge, where a minus sign sits, is known to be in the box.
    Side spin must also be what back spin and total spin leave for it, when both of those
    were read."""
    counted = [
        r for r in reads if agrees(r, values, MIN_AGREE) and r.get(ANCHOR) == values.get(ANCHOR)
    ]
    out, votes = {}, {}
    for field in LEFT_FIELDS:
        out[field], votes[field] = vote([r.get(field) for r in counted])
    if out["side_spin"] is not None and not _spin_fits(out["side_spin"], values):
        out["side_spin"], votes["side_spin"] = None, 0
    return out, votes


def _spin_fits(side: str, values: dict[str, str | None]) -> bool:
    """Whether side spin squared is total spin squared less back spin squared, as far as
    three whole numbers can tell. True when back or total spin was not read."""
    back, total = values.get("back_spin"), values.get("total_spin")
    if back is None or total is None:
        return True
    s, b, t = abs(int(side)), int(back), int(total)
    return math.fabs(s * s - (t * t - b * b)) <= s + b + t + 1


def _best_match(area: np.ndarray, patch: np.ndarray) -> tuple[float, float] | None:
    """Where `patch` matches `area` best over SCALES, as the middle of the match, or None
    when no match reaches MIN_MATCH."""
    best, where = MIN_MATCH, None
    for scale in SCALES:
        w, h = round(patch.shape[1] * scale), round(patch.shape[0] * scale)
        if w < 4 or h < 4 or w > area.shape[1] or h > area.shape[0]:
            continue
        scaled = cv2.resize(patch, (w, h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(area, scaled, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(result)
        if score > best:
            best, where = score, (x + w / 2, y + h / 2)
    return where
