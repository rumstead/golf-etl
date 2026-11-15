"""The left panel against a frame of IMG_4877 that shows both panels."""

import numpy as np
import pytest

from golf_etl.sim import FIELDS, PANEL_REFERENCE, Glyphs, find_panel, read_cell, value_boxes
from golf_etl.sim_left import (
    GRID,
    LEFT_FIELDS,
    SHARED,
    agrees,
    find_left_panel,
    left_reads,
    pick_left_panel,
    read_left,
    settle_left_panel,
)
from tests.sim_fixtures import load

LEFT_BOX = (44, 183, 55, 296)


@pytest.fixture(scope="module")
def glyphs() -> Glyphs:
    return Glyphs.load(PANEL_REFERENCE.with_name("sim_glyphs.npz"))


@pytest.fixture(scope="module")
def both(glyphs):
    """The frame, its labels, the readout panel found in it and what that panel reads."""
    rgb, labels = load("4877-both")
    panel = find_panel(rgb)
    assert panel is not None
    cells = zip(FIELDS, value_boxes(panel.box), strict=True)
    values = {f: read_cell(rgb[y0:y1, x0:x1], f, glyphs) for f, (x0, x1, y0, y1) in cells}
    return rgb, labels, panel, values


def _left_cell(rgb: np.ndarray, field: str) -> np.ndarray:
    x0, x1, y0, y1 = value_boxes(LEFT_BOX)[GRID.index(field)]
    return rgb[y0:y1, x0:x1]


def test_the_readout_panel_reads_every_number_the_left_panel_shares(both):
    _, labels, _, values = both
    assert {f: values[f] for f in SHARED if f != "peak_height"} == {
        f: labels[f] for f in SHARED if f != "peak_height"
    }


def test_finds_the_left_panel_from_the_numbers_both_panels_show(both, glyphs):
    rgb, _, panel, values = both
    box = find_left_panel(rgb, panel, values, glyphs)
    assert box is not None
    assert all(abs(a - b) <= 6 for a, b in zip(box, LEFT_BOX, strict=True))


def test_no_left_panel_when_it_shows_other_numbers(both, glyphs):
    rgb, _, panel, values = both
    other = values | {"club_speed": "73.6", "back_spin": "4670", "club_path": "2.3 I-O"}
    assert find_left_panel(rgb, panel, other, glyphs) is None


def test_no_left_panel_when_it_is_covered(both, glyphs):
    rgb, _, panel, values = both
    covered = rgb.copy()
    covered[:, :200] = (180, 150, 130)
    assert find_left_panel(covered, panel, values, glyphs) is None


def test_reads_the_left_panel_with_no_wrong_number(both, glyphs):
    rgb, labels, _, values = both
    reads = left_reads(rgb, LEFT_BOX, glyphs)
    assert {f: r for f, r in reads.items() if r is not None and r != labels[f]} == {}
    assert {f: reads[f] for f in LEFT_FIELDS} == {f: labels[f] for f in LEFT_FIELDS}
    assert agrees(reads, values, 3)


def test_a_minus_sign_reads_only_on_a_signed_field(both, glyphs):
    rgb, _, _, _ = both
    assert read_cell(_left_cell(rgb, "hla"), "hla", glyphs) == "-1.7"
    assert read_cell(_left_cell(rgb, "hla"), "vla", glyphs) is None
    assert read_cell(_left_cell(rgb, "side_spin"), "back_spin", glyphs) is None


def test_a_number_whose_minus_sign_the_box_cuts_into_reads_nothing(both, glyphs):
    rgb, _, _, _ = both
    x0, x1, y0, y1 = value_boxes(LEFT_BOX)[GRID.index("hla")]
    reads = set()
    for cut in range(1, 16):
        reads.add(read_cell(rgb[y0:y1, x0 + cut : x1], "hla", glyphs))
    assert reads <= {"-1.7", None}


def test_settling_moves_a_box_that_is_off_to_where_every_number_reads(both, glyphs):
    rgb, labels, _, values = both
    off = (LEFT_BOX[0] - 3, LEFT_BOX[1] + 2, LEFT_BOX[2] - 4, LEFT_BOX[3] + 3)
    settled = settle_left_panel([(rgb, values)], off, glyphs)
    reads = left_reads(rgb, settled, glyphs)
    assert {f: reads[f] for f in LEFT_FIELDS} == {f: labels[f] for f in LEFT_FIELDS}


def test_pick_left_panel_needs_two_boxes_that_agree():
    assert pick_left_panel([]) is None
    assert pick_left_panel([(10, 150, 20, 260)]) is None
    assert pick_left_panel([(10, 150, 20, 260), (40, 180, 20, 260)]) is None
    boxes = [(10, 150, 20, 260), (12, 152, 21, 262), (300, 440, 20, 260)]
    assert pick_left_panel(boxes) == (11, 151, 20, 261)


SHOT = {"club_speed": "82.8", "back_spin": "4267", "total_spin": "4468", "club_path": "1.1 I-O"}
LEFT = {"side_spin": "-1326", "vla": "16.6", "hla": "-1.7", "offline": "-22.5"}


def test_read_left_counts_only_frames_that_show_the_shot():
    this = {"club_speed": "82.8", "back_spin": "4267"} | LEFT
    previous = {"club_speed": "77.6", "back_spin": "4505", "side_spin": "-702", "vla": "18.1"}
    hidden = {"club_speed": "82.8", "vla": "99.9"}  # one shared number is not enough
    # without back spin the column the left fields are in is not known to be in the box
    off = {"club_speed": "82.8", "club_path": "1.1 I-O", "side_spin": "1326", "hla": "1.7"}
    frames = [this, this, previous, previous, previous, hidden, off, off, off]
    values, votes = read_left(frames, SHOT)
    assert values == LEFT
    assert votes == dict.fromkeys(LEFT, 2)


def test_read_left_needs_two_frames_for_a_number():
    this = {"club_speed": "82.8", "back_spin": "4267"}
    values, _ = read_left([this | LEFT, this | {"vla": "16.6"}], SHOT)
    assert values == {"side_spin": None, "vla": "16.6", "hla": None, "offline": None}


def test_side_spin_that_back_and_total_spin_do_not_leave_room_for_is_blank():
    this = {"club_speed": "82.8", "back_spin": "4267"} | LEFT | {"side_spin": "-326"}
    values, votes = read_left([this, this], SHOT)
    assert values["side_spin"] is None and votes["side_spin"] == 0
    assert values["vla"] == "16.6"


def test_side_spin_stands_when_total_spin_was_not_read():
    this = {"club_speed": "82.8", "back_spin": "4267"} | LEFT | {"side_spin": "-326"}
    values, _ = read_left([this, this], SHOT | {"total_spin": None})
    assert values["side_spin"] == "-326"
