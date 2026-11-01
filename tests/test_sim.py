import json

import cv2
import numpy as np
import pytest

from golf_etl.sim import (
    DIGIT_HEIGHT,
    FIELDS,
    THRESHOLDS,
    UPSCALE,
    Glyph,
    Glyphs,
    Panel,
    SimShot,
    _read,
    _split,
    before_window,
    build_glyphs,
    counts_settled,
    cut_glyphs,
    find_panel,
    pick_panel,
    read_cell,
    read_shot,
    shot_window,
    value_boxes,
    vote,
    write_sim_json,
)
from tests.sim_fixtures import load

MARGIN = 40  # px around the panel in every fixture
PANEL_SIZES = {  # (width, height) of the panel in the source video
    "4761-s1": (126, 243),
    "4762-s8": (126, 243),
    "4758-s1-white": (104, 204),
    "4758-s2-tinted": (104, 204),
}
# (x0, x1, y0, y1) where the cell grid of the reference panel lines up with the fixture's cells.
# The 4762 panel sits 2 to 3 px up and right of the MARGIN box, and the 4758 panel is 107 x 211.
PANEL_BOXES = {
    "4761-s1": (40, 166, 40, 283),
    "4762-s8": (42, 168, 38, 281),
    "4758-s1-white": (42, 149, 41, 252),
    "4758-s2-tinted": (42, 149, 41, 252),
}
# IMG_4758 frames cut to (1170, 1380, 200, 520) at 119 s and (1190, 1360, 225, 485) at 72 s
ROW_LOW_BOX = (51, 158, 48, 259)
NO_LABELS_BOX = (31, 138, 23, 234)
READS_BLANK = {
    ("4758-s2-tinted", "club_path"),  # the dim 4 breaks apart in this single tinted frame
    ("4758-s1-white", "club_speed"),  # a stray dot between the 9 and the 1 is not a decimal point
    ("4758-s2-tinted", "ball_speed"),  # the 2 and the 5 match no class clearly at any threshold
    ("4758-s2-tinted", "total_spin"),  # a dot after the last digit, and spin has no decimals
}


# (x0, x1, y0, y1) px added to the panel box: moved 2px each way, then grown and shrunk 2px
BOX_ERRORS = [
    (2, 2, 0, 0),
    (-2, -2, 0, 0),
    (0, 0, 2, 2),
    (0, 0, -2, -2),
    (-2, 2, -2, 2),
    (2, -2, 2, -2),
]


def _cells(
    name: str, box_error: tuple[int, int, int, int] = (0, 0, 0, 0)
) -> tuple[dict[str, np.ndarray], dict[str, str | None]]:
    """The value cell of every field, by field name, and the fixture's labels."""
    rgb, labels = load(name)
    w, h = PANEL_SIZES[name]
    panel = (MARGIN, MARGIN + w, MARGIN, MARGIN + h)
    boxes = value_boxes(tuple(a + b for a, b in zip(panel, box_error, strict=True)))
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


@pytest.mark.parametrize("name", PANEL_SIZES)
def test_a_panel_box_off_by_two_pixels_reads_no_wrong_number(name, glyphs):
    for box_error in BOX_ERRORS:
        cells, labels = _cells(name, box_error)
        for f in FIELDS:
            if labels[f]:
                assert read_cell(cells[f], f, glyphs) in (labels[f], None), (box_error, f)


@pytest.mark.parametrize(
    ("name", "field"),
    [
        ("4761-s1", "face_to_target"),
        ("4758-s1-white", "face_to_path"),
        ("4761-s1", "club_path"),
        ("4762-s8", "club_path"),
        ("4758-s1-white", "club_path"),
    ],
)
def test_a_suffix_field_without_its_last_glyph_reads_nothing(name, field, glyphs):
    cells, labels = _cells(name)
    cell = cells[field]
    assert read_cell(cell, field, glyphs) == labels[field]
    digit = DIGIT_HEIGHT * UPSCALE * cell.shape[0]
    for threshold in THRESHOLDS:
        cut = cut_glyphs(cell, threshold)
        last = max(i for i, g in enumerate(cut) if isinstance(g, Glyph))
        assert _read(cut[:last] + cut[last + 1 :], field, glyphs, digit) is None, threshold


def test_a_number_with_no_space_does_not_read_as_a_face(glyphs):
    # "110.0" is what "11.0 C" looks like with the C lost; its widest gap is the decimal point
    cells, _ = _cells("4761-s1")
    assert read_cell(cells["carry_raw"], "face_to_path", glyphs) is None


def test_a_blob_too_narrow_for_its_digit_count_is_not_cut():
    # 5px wide at a 1px digit step asks for 5 digits, and the last cut has no room
    assert _split((0, 0, 5, 2), np.ones((2, 5)), digit=2.0) is None


def test_an_empty_cell_reads_nothing(glyphs):
    assert read_cell(np.zeros((0, 0, 3), np.uint8), "smash", glyphs) is None


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


@pytest.mark.parametrize("name", PANEL_SIZES)
def test_finds_the_panel_in_each_fixture(name):
    panel = find_panel(load(name)[0])
    assert panel is not None
    errors = [a - b for a, b in zip(panel.box, PANEL_BOXES[name], strict=True)]
    assert all(abs(e) <= 2 for e in errors), (panel.box, errors)


def test_finds_a_scaled_panel_pasted_into_a_blank_frame():
    rgb, _ = load("4761-s1")
    w, h = PANEL_SIZES["4761-s1"]
    crop = rgb[MARGIN : MARGIN + h, MARGIN : MARGIN + w]
    small = cv2.resize(crop, (round(w * 0.8), round(h * 0.8)), interpolation=cv2.INTER_AREA)
    frame = np.empty((1080, 1920, 3), np.uint8)
    frame[:] = (60, 90, 60)
    frame[300 : 300 + small.shape[0], 1500 : 1500 + small.shape[1]] = small
    panel = find_panel(frame)
    assert panel is not None
    want = (1500, 1500 + small.shape[1], 300, 300 + small.shape[0])
    errors = [a - b for a, b in zip(panel.box, want, strict=True)]
    assert all(abs(e) <= 2 for e in errors), (panel.box, want)


def test_no_panel_in_a_frame_without_a_screen():
    assert find_panel(load("range")[0]) is None


def test_no_panel_in_the_frame_without_a_screen_that_scores_highest():
    # IMG_4626 at 15 s: a door frame on a white wall
    assert find_panel(load("wall")[0]) is None


def test_finds_the_panel_when_the_best_coarse_hit_is_a_row_low():
    # IMG_4758 at 119 s: at half size the panel matches best one row low
    panel = find_panel(load("4758-row-low")[0])
    assert panel is not None
    errors = [a - b for a, b in zip(panel.box, ROW_LOW_BOX, strict=True)]
    assert all(abs(e) <= 2 for e in errors), (panel.box, errors)


def test_finds_the_panel_when_the_labels_are_hidden():
    # IMG_4758 at 72 s, kept gray: the projector color hides the labels under the values
    panel = find_panel(load("4758-no-labels")[0])
    assert panel is not None
    errors = [a - b for a, b in zip(panel.box, NO_LABELS_BOX, strict=True)]
    assert all(abs(e) <= 2 for e in errors), (panel.box, errors)


def test_an_empty_frame_has_no_panel():
    assert find_panel(np.zeros((0, 0, 3), np.uint8)) is None


def test_a_float_frame_finds_the_same_panel():
    rgb, _ = load("4761-s1")
    assert find_panel(rgb.astype(np.float32)) == find_panel(rgb)


@pytest.mark.parametrize("name", PANEL_SIZES)
def test_reads_through_the_found_box_give_no_wrong_number(name, glyphs):
    rgb, labels = load(name)
    panel = find_panel(rgb)
    assert panel is not None
    boxes = value_boxes(panel.box)
    for f, (x0, x1, y0, y1) in zip(FIELDS, boxes, strict=True):
        if labels[f]:
            assert read_cell(rgb[y0:y1, x0:x1], f, glyphs) in (labels[f], None), f


# IMG_4758 frames cut to the found panel box plus 12 px: 4758-fragments at 48.5 s,
# 4758-merged-point at 45.5 s and 4758-vanished-digit at 46.0 s
REAL_BOX = (12, 120, 12, 223)


def _real_cell(name: str, field: str) -> tuple[np.ndarray, str]:
    rgb, labels = load(name)
    x0, x1, y0, y1 = value_boxes(REAL_BOX)[FIELDS.index(field)]
    return rgb[y0:y1, x0:x1], labels[field]


@pytest.mark.parametrize("field", ["carry_raw", "back_spin", "total_spin"])
def test_a_digit_broken_into_marks_reads_nothing(field, glyphs):
    # the 9 and 6 of 196.2 and the 4 of 3142 break into dots and dashes
    cell, _ = _real_cell("4758-fragments", field)
    assert read_cell(cell, field, glyphs) is None


@pytest.mark.parametrize("field", ["carry_raw", "ball_speed"])
def test_a_decimal_point_stuck_to_a_digit_is_cut_off(field, glyphs):
    # the point touches the 6 of 196.2 and the 5 of 124.5
    cell, label = _real_cell("4758-merged-point", field)
    assert read_cell(cell, field, glyphs) == label


def test_a_vanished_digit_reads_nothing(glyphs):
    # the 0 of 107.7 is gone at the higher thresholds
    cell, _ = _real_cell("4758-vanished-digit", "peak_height")
    assert read_cell(cell, "peak_height", glyphs) is None


def test_a_weak_read_from_one_threshold_reads_nothing(glyphs):
    # IMG_4758 at 138.5 s, the total_spin cell: only the 0.8 cut reads, and its broken 8 reads 9
    cell, _ = load("4758-lone-read")
    assert read_cell(cell, "total_spin", glyphs) is None


def test_vote_ignores_a_stale_value_outnumbered_by_the_new_one():
    assert vote(["2.0 C", "2.0 C", "5.5 C", "5.5 C", "5.5 C", "5.5 C", "5.5 C"]) == ("5.5 C", 5)


def test_vote_leaves_a_close_split_blank():
    assert vote(["110.0", "110.0", "118.0", "118.0", "118.0"]) == (None, 0)


def test_one_frame_is_not_enough_to_vote():
    assert vote(["1.43", None]) == (None, 0)


def test_shot_window_ends_at_the_next_swings_clip():
    assert shot_window(100.0, 600.0, 106.0, 2.5) == (102.0, 103.5)
    assert shot_window(100.0, 600.0, None, 2.5) == (102.0, 115.0)
    assert shot_window(100.0, 104.0, None, 2.5) == (102.0, 104.0)


def _shot_frames(times=(2.0, 2.5, 3.0)):
    rgb, labels = load("4761-s1")
    return [(t, rgb) for t in times], labels


def test_read_shot_reads_every_labeled_field_from_repeated_frames(glyphs):
    frames, labels = _shot_frames()
    shot = read_shot(frames, Panel(PANEL_BOXES["4761-s1"], 0.5), glyphs)
    assert shot.frames == 3
    want = {f: None if ("4761-s1", f) in READS_BLANK else labels[f] for f in FIELDS}
    assert shot.values == want
    for f in FIELDS:
        assert shot.votes[f] == (3 if want[f] else 0)
        assert shot.first_seen_s.get(f) == (2.0 if want[f] else None)


def test_a_screen_that_shows_the_same_numbers_before_impact_reads_blank(glyphs):
    frames, _ = _shot_frames()
    before = [(1.0, frames[0][1]), (1.5, frames[0][1])]
    shot = read_shot(frames, Panel(PANEL_BOXES["4761-s1"], 0.5), glyphs, before)
    assert shot.unchanged
    assert shot.values == dict.fromkeys(FIELDS)
    assert shot.votes == dict.fromkeys(FIELDS, 0)
    assert shot.first_seen_s == {}
    assert shot.frames == 3


def test_a_screen_that_shows_another_shot_before_impact_reads_the_new_one(glyphs):
    frames, labels = _shot_frames()
    rgb, _ = load("4762-s8")  # another shot's panel, 2 px off the box
    shot = read_shot(frames, Panel(PANEL_BOXES["4761-s1"], 0.5), glyphs, [(1.0, rgb)] * 2)
    assert not shot.unchanged
    assert shot.values["club_speed"] == labels["club_speed"]


def test_no_reads_before_impact_leave_the_shot_as_read(glyphs):
    frames, labels = _shot_frames()
    blank = np.empty_like(frames[0][1])
    blank[:] = frames[0][1][0, 0]
    shot = read_shot(frames, Panel(PANEL_BOXES["4761-s1"], 0.5), glyphs, [(1.0, blank)] * 2)
    assert not shot.unchanged
    assert shot.values["club_speed"] == labels["club_speed"]


def test_before_window_stays_clear_of_the_previous_impact_and_the_video_start():
    assert before_window(100.0, 80.0) == (98.5, 99.75)
    assert before_window(100.0, 99.0) == (100.0, 99.75)  # empty: the previous shot just landed
    assert before_window(1.0, None) == (0.0, 0.75)


def _counting_frames():
    """4761-s1 frames with carry_raw's 110.0 drawn over carry_game and total_length."""
    rgb, _ = load("4761-s1")
    rgb = rgb.copy()
    boxes = dict(zip(FIELDS, value_boxes(PANEL_BOXES["4761-s1"]), strict=True))
    x0, x1, y0, y1 = boxes["carry_raw"]
    for f in ("carry_game", "total_length"):
        bx0, _, by0, _ = boxes[f]
        rgb[by0 : by0 + y1 - y0, bx0 : bx0 + x1 - x0] = rgb[y0:y1, x0:x1]
    return [(t, rgb) for t in (102.0, 102.5, 103.0)]


def test_counting_fields_are_blank_when_the_window_ends_before_they_settle(glyphs):
    frames = _counting_frames()
    panel = Panel(PANEL_BOXES["4761-s1"], 0.5)
    _, end = shot_window(100.0, 600.0, 112.0, 2.5)  # the next swing's clip starts at 109.5
    shot = read_shot(frames, panel, glyphs, settled=counts_settled(100.0, end))
    assert shot.values["carry_game"] is None and shot.values["total_length"] is None
    assert shot.votes["total_length"] == 0 and "total_length" not in shot.first_seen_s
    assert shot.values["carry_raw"] == "110.0"
    _, end = shot_window(100.0, 600.0, None, 2.5)
    shot = read_shot(frames, panel, glyphs, settled=counts_settled(100.0, end))
    assert shot.values["carry_game"] == shot.values["total_length"] == "110.0"


def test_sim_json_has_every_field(tmp_path, glyphs):
    frames, labels = _shot_frames()
    panel = Panel(PANEL_BOXES["4761-s1"], 0.5)
    shot = read_shot(frames, panel, glyphs)
    write_sim_json(tmp_path / "sim.json", shot, panel)
    data = json.loads((tmp_path / "sim.json").read_text())
    assert data["panel"] == list(panel.box)
    assert data["frames"] == 3
    assert data["unchanged"] is False
    assert list(data["fields"]) == list(FIELDS)
    for f in FIELDS:
        entry = data["fields"][f]
        assert entry["text"] == shot.values[f]
        if entry["text"] is None:
            assert entry["value"] is None
            assert entry["seen_s"] is None
        else:
            assert entry["value"] == float(entry["text"].split()[0])
            assert entry["seen_s"] == 2.0
        assert entry["votes"] == shot.votes[f]


def test_sim_json_value_is_the_leading_number(tmp_path):
    shot = SimShot(
        values={"club_path": "4.0 I-O", "back_spin": "3837"},
        votes={"club_path": 3, "back_spin": 2},
        frames=3,
        first_seen_s={"club_path": 2.5, "back_spin": 2.0},
    )
    write_sim_json(tmp_path / "sim.json", shot, Panel((1, 2, 3, 4), 0.5))
    fields = json.loads((tmp_path / "sim.json").read_text())["fields"]
    assert fields["club_path"] == {"text": "4.0 I-O", "value": 4.0, "votes": 3, "seen_s": 2.5}
    assert fields["back_spin"]["value"] == 3837.0


TRUE_BOX = (40, 166, 40, 283)


def _panel(box, score=0.5):
    return Panel(box, score)


def test_pick_panel_takes_the_box_most_frames_agree_on():
    true = [
        _panel((40 + dx, 166 + dx, 40 + dy, 283 + dy), 0.4)
        for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1), (0, 0)]
    ]
    low = [_panel((40, 166, 75, 318), 0.9)] * 2
    assert pick_panel(low + true) in true


def test_pick_panel_needs_three_agreeing_frames():
    assert pick_panel([_panel(TRUE_BOX), _panel(TRUE_BOX), None, None, None]) is None


def test_pick_panel_needs_a_majority():
    other = (400, 526, 300, 543)
    assert pick_panel([_panel(TRUE_BOX)] * 3 + [_panel(other)] * 3) is None


def test_pick_panel_keeps_a_drifting_panel():
    panels = [_panel((40, 166, y, y + 243)) for y in (199, 200, 201)]
    assert pick_panel(panels) is panels[1]


def test_pick_panel_with_no_panel_is_none():
    assert pick_panel([None, None]) is None
