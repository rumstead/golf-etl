import numpy as np
import pytest

from golf_etl.sim import FIELDS, Glyphs, build_glyphs, read_cell, value_boxes
from tests.sim_fixtures import load

MARGIN = 40  # px around the panel in every fixture
PANEL_SIZES = {  # (width, height) of the panel in the source video
    "4761-s1": (126, 243),
    "4762-s8": (126, 243),
    "4758-s1-white": (104, 204),
    "4758-s2-tinted": (104, 204),
}
# The dim 4 breaks apart in this single tinted frame, so the cell reads blank.
READS_BLANK = {("4758-s2-tinted", "club_path")}


def _cells(name: str) -> tuple[dict[str, np.ndarray], dict[str, str | None]]:
    """The value cell of every field, by field name, and the fixture's labels."""
    rgb, labels = load(name)
    w, h = PANEL_SIZES[name]
    boxes = value_boxes((MARGIN, MARGIN + w, MARGIN, MARGIN + h))
    cells = {f: rgb[y0:y1, x0:x1] for f, (x0, x1, y0, y1) in zip(FIELDS, boxes, strict=True)}
    return cells, labels


@pytest.fixture(scope="module")
def glyphs() -> Glyphs:
    samples = []
    for name in PANEL_SIZES:
        cells, labels = _cells(name)
        samples += [(cells[f], labels[f]) for f in FIELDS if labels[f]]
    return build_glyphs(samples)


@pytest.mark.parametrize("name", PANEL_SIZES)
def test_reads_every_labeled_cell_of_each_fixture(name, glyphs):
    cells, labels = _cells(name)
    labeled = [f for f in FIELDS if labels[f]]
    reads = {f: read_cell(cells[f], f, glyphs) for f in labeled}
    assert reads == {f: None if (name, f) in READS_BLANK else labels[f] for f in labeled}


def test_dashes_read_as_nothing(glyphs):
    cells, _ = _cells("4762-s8")
    assert read_cell(cells["total_length"], "total_length", glyphs) is None


def test_a_covered_cell_reads_nothing_and_the_rest_still_read(glyphs):
    cells, labels = _cells("4761-s1")
    left = FIELDS[0::2]
    right = [f for f in FIELDS[1::2] if labels[f]]
    for f in left:
        median = np.median(cells[f].reshape(-1, 3), axis=0)
        cells[f] = np.empty_like(cells[f])
        cells[f][:] = median
    assert [read_cell(cells[f], f, glyphs) for f in left] == [None] * len(left)
    assert [read_cell(cells[f], f, glyphs) for f in right] == [labels[f] for f in right]


def test_suffix_letters_and_digits_are_told_apart(glyphs):
    cells, _ = _cells("4761-s1")
    assert read_cell(cells["face_to_target"], "face_to_target", glyphs) == "4.6 C"
    cells, _ = _cells("4758-s1-white")
    assert read_cell(cells["face_to_path"], "face_to_path", glyphs) == "1.2 O"


def test_glyphs_round_trip_through_npz(tmp_path, glyphs):
    glyphs.save(tmp_path / "glyphs.npz")
    back = Glyphs.load(tmp_path / "glyphs.npz")
    np.testing.assert_array_equal(back.images, glyphs.images)
    np.testing.assert_array_equal(back.aspects, glyphs.aspects)
    np.testing.assert_array_equal(back.labels, glyphs.labels)
    cells, labels = _cells("4762-s8")
    assert read_cell(cells["smash"], "smash", back) == labels["smash"]
