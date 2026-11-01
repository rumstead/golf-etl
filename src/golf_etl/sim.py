"""Read the golf simulator's readout panel with glyph templates, no model.

The panel is a 2 x 6 grid of numbers on a flat background. Slo-mo footage catches the projector's
color cycle, so the text can be white, yellow or blue: each value cell is read as its distance
from the cell's own median color, cut into glyphs, and every glyph is matched to labeled
templates. Small text often loses its decimal point, so the point is put back from the field's
decimals. The panel itself is found by matching the edges of a reference panel over a range of
sizes.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache, cached_property
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np

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
)  # panel order: left to right, then top to bottom
COLUMNS, ROWS = 2, 6
CELL_INSET_X, CELL_INSET_TOP = 3, 2  # px trimmed off each cell before reading
VALUE_SHARE = 0.72  # top share of a cell that holds the value, the label sits below

UPSCALE = 4
PERCENTILES = (40, 98)  # background and text levels of the distance image
THRESHOLDS = (0.7, 0.75, 0.8)  # of the way from background to text level
MIN_AREA = 3 * UPSCALE  # px of a blob at the upscaled size
MIN_BOTTOM = 0.3  # of the cell height; a blob ending above this is noise from the cell above
EDGE_MARGIN = UPSCALE  # px; a blob this close to the left or right edge is the cell border
# Blur can merge a whole value into one blob, so the cell sets the digit size, not the tallest blob.
DIGIT_HEIGHT = 0.57  # fraction of the value cell's height a digit fills
SPACE_GAP = 0.48  # of the digit height; the gap before a suffix is at least this wide
MARK_HEIGHT = 0.3  # of the digit height; anything shorter is a point or a dash
DOT_WIDTH = 2.2  # heights; a wider mark is a dash
SPLIT_HEIGHT = 0.6  # of the digit height; a shorter blob that is not a mark is a broken glyph
DIGIT_WIDTH = 0.52  # of the digit height, the step from one digit to the next
BROKEN = "?"  # a piece of a glyph the threshold broke apart; the read at that threshold fails
CUT_SEARCH = 0.25  # digit widths around an even split searched for the darkest column
GLYPH_SIZE = (12, 20)  # width, height
ASPECT_WEIGHT = 0.5
CLASS_MARGIN = 0.10  # score a glyph's class must beat every other class by, 0 and O as one

CLASSES = "0123456789ICO"
SUFFIX_LENGTH = {"club_path": 2, "face_to_path": 1, "face_to_target": 1}
DECIMALS = {"smash": 2, "back_spin": 0, "total_spin": 0}  # every other field has 1
FORMATS = {
    "club_path": r"\d+\.\d (I-O|O-I)",
    "face_to_path": r"\d+\.\d [CO]",
    "face_to_target": r"\d+\.\d [CO]",
    "smash": r"\d\.\d\d",
    "back_spin": r"\d+",
    "total_spin": r"\d+",
}  # every other field is r"\d+\.\d"

PANEL_REFERENCE = Path(__file__).with_name("sim_panel.png")  # edges of a 126 x 243 panel
CANNY = (50, 150)
EDGE_BLUR = 1.0  # px sigma, so edges a px apart still match
COARSE_SIZE = 0.5  # frame scale for the coarse pass
COARSE_SCALES = np.round(np.arange(0.6, 1.41, 0.05), 2)  # of the reference size
COARSE_HITS = 3  # best coarse hits refined at full size
HIT_SPACING = 0.5  # of the panel height between coarse hits
FINE_SCALES = np.round(np.arange(-0.05, 0.051, 0.01), 2)  # around a coarse hit's scale
HEIGHT_SCALES = np.round(np.arange(-0.05, 0.051, 0.01), 2)  # height scale less width scale
ROI_MARGIN = 0.25  # of the coarse box height searched around it at full size, more than a row
LABEL_BAND = (0.6, 0.86)  # of a panel row, where the labels sit under the value
SETTLE_REACH = 0.33  # of a panel row, how far the box may move up or down as it settles
MIN_PANEL_SCORE = 0.29  # halfway between the weakest panel (0.335) and best no-panel frame (0.246)

Box = tuple[int, int, int, int]  # x0, x1, y0, y1


@dataclass(frozen=True)
class Panel:
    box: Box
    score: float  # TM_CCOEFF_NORMED of the frame's edges against the reference, before settling


@dataclass(frozen=True)
class Glyph:
    image: np.ndarray  # 20 x 12 float32, mean removed, unit norm
    aspect: float  # width / height of the glyph's blob
    left: int  # upscaled px from the cell's left edge
    right: int


@dataclass(frozen=True)
class Glyphs:
    """Labeled glyph templates. `images` is the raw crop scaled to 0 to 255."""

    images: np.ndarray  # N x 20 x 12 uint8
    aspects: np.ndarray  # N float32
    labels: np.ndarray  # N <U1, one of CLASSES

    @cached_property
    def features(self) -> np.ndarray:
        """N x 240 float32: each image with its mean removed and scaled to unit norm."""
        return np.array([_normalize(image.ravel()) for image in self.images])

    def save(self, path: Path) -> None:
        np.savez_compressed(path, images=self.images, aspects=self.aspects, labels=self.labels)

    @classmethod
    def load(cls, path: Path) -> "Glyphs":
        with np.load(path) as data:
            return cls(data["images"], data["aspects"], data["labels"])


def value_boxes(panel_box: Box) -> list[Box]:
    """The value part of each cell in FIELDS order, in frame px."""
    x0, x1, y0, y1 = panel_box
    boxes = []
    for row in range(ROWS):
        for col in range(COLUMNS):
            cx0 = x0 + (x1 - x0) * col // COLUMNS + CELL_INSET_X
            cx1 = x0 + (x1 - x0) * (col + 1) // COLUMNS - CELL_INSET_X
            cy0 = y0 + (y1 - y0) * row // ROWS + CELL_INSET_TOP
            cy1 = y0 + (y1 - y0) * (row + 1) // ROWS
            boxes.append((cx0, cx1, cy0, cy0 + int((cy1 - cy0) * VALUE_SHARE)))
    return boxes


def find_panel(frame: np.ndarray) -> Panel | None:
    """The readout panel in an RGB frame (0 to 255), or None when nothing matches well enough."""
    panel = _best_panel(frame)
    return panel if panel and panel.score >= MIN_PANEL_SCORE else None


def cut_glyphs(cell: np.ndarray, threshold: float = THRESHOLDS[1]) -> list[str | Glyph]:
    """The cell's glyphs left to right.

    "." or "-" for small marks, BROKEN for a piece of a glyph the threshold broke apart, else a
    Glyph.
    """
    return _cut(_distance(cell), threshold) if cell.size else []


def build_glyphs(samples: Iterable[tuple[np.ndarray, str]], per_class: int = 80) -> Glyphs:
    """Templates from (cell RGB, screen text) pairs.

    Each threshold's cut of a cell is used only when its glyph count matches the text's letters
    and digits. At most `per_class` templates are kept per class, spread over the input order.
    """
    found: dict[str, list[Glyph]] = {c: [] for c in CLASSES}
    for cell, text in samples:
        chars = [c for c in text if c.isalnum()]
        for cut in _cuts(cell):
            glyphs = [g for g in cut if isinstance(g, Glyph)]
            if BROKEN not in cut and len(glyphs) == len(chars):
                for char, glyph in zip(chars, glyphs, strict=True):
                    found[char].append(glyph)
    kept = [(c, g) for c in CLASSES for g in _spread(found[c], per_class)]
    return Glyphs(
        images=np.array([_to_uint8(g.image) for _, g in kept], dtype=np.uint8),
        aspects=np.array([g.aspect for _, g in kept], dtype=np.float32),
        labels=np.array([c for c, _ in kept], dtype="<U1"),
    )


def read_cell(cell: np.ndarray, field: str, glyphs: Glyphs) -> str | None:
    """The cell's text as the screen shows it, or None when no clear read fits the field."""
    digit = DIGIT_HEIGHT * UPSCALE * cell.shape[0]
    reads = [text for cut in _cuts(cell) if (text := _read(cut, field, glyphs, digit))]
    top = Counter(reads).most_common(2)
    if not top or (len(top) == 2 and top[0][1] == top[1][1]):
        return None
    return top[0][0]


def _best_panel(frame: np.ndarray) -> Panel | None:
    """The best match of the reference, whatever its score.

    The reference is matched over COARSE_SCALES on a COARSE_SIZE frame. The best COARSE_HITS hits
    that sit apart are each refined at full size, and the best refined match wins.
    """
    if not frame.size:
        return None
    edges = _edges(frame)
    small = cv2.resize(edges, None, fx=COARSE_SIZE, fy=COARSE_SIZE, interpolation=cv2.INTER_AREA)
    hits = [
        (score, box, scale)
        for scale in COARSE_SCALES
        for score, box in _peaks(small, scale * COARSE_SIZE, scale * COARSE_SIZE, COARSE_HITS)
    ]
    starts: list[tuple[Box, float]] = []
    for _, box, scale in sorted(hits, key=lambda h: -h[0]):
        if all(_apart(box, other) for other, _ in starts):
            starts.append((box, scale))
        if len(starts) == COARSE_HITS:
            break
    panels = [p for box, scale in starts if (p := _refine(edges, box, scale))]
    return max(panels, key=lambda p: p.score, default=None)


def _refine(edges: np.ndarray, coarse_box: Box, scale: float) -> Panel | None:
    """The best match near a coarse hit: over FINE_SCALES, then over HEIGHT_SCALES for the height.

    The box then settles where the whole panel and its value rows line up best together.
    """
    x0, x1, y0, y1 = (round(v / COARSE_SIZE) for v in coarse_box)
    margin = round(ROI_MARGIN * (y1 - y0)) + 2
    left, top = max(0, x0 - margin), max(0, y0 - margin)
    roi = edges[top : y1 + margin, left : x1 + margin]
    fine = [(hit, s) for s in scale + FINE_SCALES for hit in _peaks(roi, s, s)]
    if not fine:
        return None
    _, scale = max(fine, key=lambda f: f[0][0])
    tall = [hit for sy in scale + HEIGHT_SCALES for hit in _peaks(roi, scale, sy)]
    score, (bx0, bx1, by0, by1) = max(tall, key=lambda h: h[0])
    return Panel(_settle(edges, (bx0 + left, bx1 + left, by0 + top, by1 + top)), score)


def _settle(edges: np.ndarray, box: Box) -> Box:
    """The box moved up to SETTLE_REACH of a row up or down and 2 px sideways.

    Some projector colors hide the labels. The whole panel then fits best a quarter of a row high,
    and where the labels show, the value rows alone fit best a quarter of a row low. The sum of the
    two scores peaks on the panel in both cases.
    """
    x0, x1, y0, y1 = box
    w, h = x1 - x0, y1 - y0
    reach = round(SETTLE_REACH * h / ROWS)
    left, top = max(0, x0 - 2), max(0, y0 - reach)
    window = edges[top : y1 + reach, left : x1 + 2]
    if window.shape[0] < h or window.shape[1] < w:
        return box
    scores = sum(
        cv2.matchTemplate(
            window, cv2.resize(r, (w, h), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED
        )
        for r in _references()
    )
    _, _, _, (x, y) = cv2.minMaxLoc(scores)
    return (left + x, left + x + w, top + y, top + y + h)


def _peaks(edges: np.ndarray, sx: float, sy: float, count: int = 1) -> list[tuple[float, Box]]:
    """Up to `count` best scores and boxes of the reference scaled sx wide and sy high.

    Each peak blanks the scores within HIT_SPACING of the panel height around it. Empty when the
    reference does not fit in `edges`.
    """
    reference, _ = _references()
    w, h = round(reference.shape[1] * sx), round(reference.shape[0] * sy)
    if w > edges.shape[1] or h > edges.shape[0]:
        return []
    template = cv2.resize(reference, (w, h), interpolation=cv2.INTER_AREA)
    scores = cv2.matchTemplate(edges, template, cv2.TM_CCOEFF_NORMED)
    reach = int(HIT_SPACING * h)
    peaks = []
    for _ in range(count):
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        peaks.append((float(score), (x, x + w, y, y + h)))
        scores[max(0, y - reach) : y + reach + 1, max(0, x - reach) : x + reach + 1] = -1
    return peaks


def _apart(a: Box, b: Box) -> bool:
    """Whether two boxes' centers are at least HIT_SPACING of the taller one's height apart."""
    dx = (a[0] + a[1] - b[0] - b[1]) / 2
    dy = (a[2] + a[3] - b[2] - b[3]) / 2
    return np.hypot(dx, dy) >= HIT_SPACING * max(a[3] - a[2], b[3] - b[2])


@cache
def _references() -> tuple[np.ndarray, np.ndarray]:
    """The blurred reference edges: the whole panel, and its value rows with the labels blanked."""
    edges = cv2.imread(str(PANEL_REFERENCE), cv2.IMREAD_GRAYSCALE)
    values = edges.copy()
    row = edges.shape[0] / ROWS
    for k in range(ROWS):
        values[round((k + LABEL_BAND[0]) * row) : round((k + LABEL_BAND[1]) * row)] = 0
    return tuple(cv2.GaussianBlur(e, (0, 0), EDGE_BLUR) for e in (edges, values))


def _edges(rgb: np.ndarray) -> np.ndarray:
    """Blurred Canny edges, strongest over the color channels. Other dtypes are clipped to uint8.

    They stay uint8: in a blurred float map, near flat areas give spurious scores close to 1.
    """
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    edges = cv2.Canny(np.ascontiguousarray(rgb), *CANNY)
    return cv2.GaussianBlur(edges, (0, 0), EDGE_BLUR)


def _cuts(cell: np.ndarray) -> list[list[str | Glyph]]:
    """The cell cut at each threshold. An empty cell has nothing to cut."""
    if not cell.size:
        return [[] for _ in THRESHOLDS]
    dist = _distance(cell)
    return [_cut(dist, threshold) for threshold in THRESHOLDS]


def _read(cut: list[str | Glyph], field: str, glyphs: Glyphs, digit: float) -> str | None:
    """One threshold's text, or None when a glyph is broken or the text does not fit the field.

    `digit` is the digit height in upscaled px.
    """
    found = [g for g in cut if isinstance(g, Glyph)]
    if BROKEN in cut or not found:
        return None
    chars = [_classify(g, glyphs) for g in found]
    if field in SUFFIX_LENGTH:
        start = _suffix_start(cut, field, digit)
        if start is None:
            return None
        chars.insert(start, " ")
    return _text(field, "".join(chars))


def _suffix_start(cut: list[str | Glyph], field: str, digit: float) -> int | None:
    """Index of the first suffix glyph, or None when the cut has no clear space before it.

    The suffix starts after the widest gap between glyphs, which must be space wide. A "." after
    the first glyph and before the suffix must sit the field's decimals left of the space; a "."
    inside the suffix is the path's dash.
    """
    found = [g for g in cut if isinstance(g, Glyph)]
    if len(found) < 2:
        return None
    gaps = [b.left - a.right for a, b in pairwise(found)]
    start = int(np.argmax(gaps)) + 1
    if gaps[start - 1] < SPACE_GAP * digit:
        return None
    point = start - DECIMALS.get(field, 1)  # glyphs left of the decimal point
    before = 0
    for item in cut:
        if isinstance(item, Glyph):
            before += 1
        elif item == "." and 0 < before <= start and before != point:
            return None
    return start


def _distance(cell: np.ndarray) -> np.ndarray:
    """Upscaled Euclidean RGB distance of each pixel from the cell's median color."""
    rgb = cell.astype(np.float32)
    background = np.median(rgb.reshape(-1, 3), axis=0)
    dist = np.linalg.norm(rgb - background, axis=2)
    return cv2.resize(dist, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)


def _cut(dist: np.ndarray, threshold: float) -> list[str | Glyph]:
    lo, hi = np.percentile(dist, PERCENTILES)
    mask = (dist > lo + threshold * (hi - lo)).astype(np.uint8)
    digit = DIGIT_HEIGHT * dist.shape[0]
    out: list[str | Glyph] = []
    for blob in _blobs(mask):
        pieces = _split(blob, dist * mask, digit)
        if pieces is None:
            out.append(BROKEN)
            continue
        for x, y, w, h in pieces:
            if h < MARK_HEIGHT * digit:
                below_middle = y + h / 2 > dist.shape[0] / 2
                out.append("." if below_middle and w < DOT_WIDTH * h else "-")
            elif h < SPLIT_HEIGHT * digit:
                out.append(BROKEN)
            else:
                out.append(_glyph(dist[y : y + h, x : x + w], x))
    return out


def _blobs(mask: np.ndarray) -> list[Box]:
    """8-connected blobs as (x, y, w, h), left to right, without noise and the cell border."""
    height, width = mask.shape
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return sorted(
        (int(x), int(y), int(w), int(h))
        for x, y, w, h, area in stats[1:]
        if area > MIN_AREA
        and y + h > MIN_BOTTOM * height
        and EDGE_MARGIN < x
        and x + w < width - EDGE_MARGIN
    )


def _split(blob: Box, ink: np.ndarray, digit: float) -> list[Box] | None:
    """Cut a blob of touching digits at the faintest column near each even split.

    None when the blob is too narrow to cut into as many digits as its width asks for.
    """
    x, y, w, h = blob
    pitch = DIGIT_WIDTH * digit
    count = round(w / pitch) if h >= SPLIT_HEIGHT * digit else 1
    if count <= 1:
        return [blob]
    columns = ink[y : y + h, x : x + w].sum(axis=0)
    reach = max(1, int(CUT_SEARCH * pitch))
    cuts = [0]
    for j in range(1, count):
        even = w * j // count
        start = max(cuts[-1] + 1, even - reach)
        stop = min(w - 1, even + reach)
        if start >= stop:
            return None
        cuts.append(start + int(np.argmin(columns[start:stop])))
    cuts.append(w)
    return [(x + a, y, b - a, h) for a, b in pairwise(cuts)]


def _glyph(crop: np.ndarray, left: int) -> Glyph:
    h, w = crop.shape
    image = cv2.resize(crop, GLYPH_SIZE, interpolation=cv2.INTER_AREA)
    return Glyph(_normalize(image), w / h, left, left + w)


def _normalize(values: np.ndarray) -> np.ndarray:
    centered = values.astype(np.float32) - values.mean()
    return centered / (np.linalg.norm(centered) + 1e-6)


def _to_uint8(image: np.ndarray) -> np.ndarray:
    lo, hi = image.min(), image.max()
    return np.round((image - lo) / max(hi - lo, 1e-6) * 255).astype(np.uint8)


def _spread(items: list[Glyph], limit: int) -> list[Glyph]:
    if len(items) <= limit:
        return items
    return [items[i] for i in np.linspace(0, len(items) - 1, limit).round().astype(int)]


def _classify(glyph: Glyph, glyphs: Glyphs) -> str:
    """The best template's class, or BROKEN when another class scores within CLASS_MARGIN."""
    scores = glyphs.features @ glyph.image.ravel()
    scores -= ASPECT_WEIGHT * np.abs(glyphs.aspects - glyph.aspect)
    best = int(np.argmax(scores))
    groups = np.where(glyphs.labels == "O", "0", glyphs.labels)
    rivals = scores[groups != groups[best]]
    if rivals.size and scores[best] - rivals.max() < CLASS_MARGIN:
        return BROKEN
    return str(glyphs.labels[best])


def _text(field: str, chars: str) -> str | None:
    """Screen text from glyph classes, or None when it does not fit the field.

    For a field with a suffix, `chars` has a space where the suffix starts.
    """
    digits, _, suffix = chars.partition(" ")
    if len(suffix) != SUFFIX_LENGTH.get(field, 0):
        return None
    digits = digits.replace("O", "0")
    suffix = suffix.replace("0", "O")
    decimals = DECIMALS.get(field, 1)
    if decimals and len(digits) > decimals:
        digits = f"{digits[:-decimals]}.{digits[-decimals:]}"
    if field == "club_path" and len(suffix) == 2:
        suffix = f"{suffix[0]}-{suffix[1]}"
    text = f"{digits} {suffix}" if suffix else digits
    return text if re.fullmatch(FORMATS.get(field, r"\d+\.\d"), text) else None
