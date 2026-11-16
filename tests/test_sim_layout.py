import numpy as np
import pytest

from golf_etl.panel import FIELDS
from golf_etl.sim import PANEL_REFERENCE, Glyphs, find_panel, value_boxes
from golf_etl.sim_layout import KINDS, broken, cell_texts, shipped_holds, solve_layout
from tests.sim_fixtures import load

KIND = {field: kind for kind, fields in KINDS.items() for field in fields}
SHOTS = [
    {
        "club_path": "1.1 I-O",
        "carry_game": "149.2",
        "face_to_path": "3.0 C",
        "carry_raw": "149.3",
        "back_spin": "4267",
        "total_length": "164.1",
        "smash": "1.29",
        "ball_speed": "106.9",
        "peak_height": "64.3",
        "total_spin": "4468",
        "face_to_target": "2.0 C",
        "club_speed": "82.8",
    },
    {
        "club_path": "2.3 I-O",
        "carry_game": "148.3",
        "face_to_path": "2.5 C",
        "carry_raw": "148.4",
        "back_spin": "4670",
        "total_length": "162.6",
        "smash": "1.44",
        "ball_speed": "105.8",
        "peak_height": "64.7",
        "total_spin": "4681",
        "face_to_target": "0.2 C",
        "club_speed": "73.6",
    },
]


def _texts(layout, shot):
    """What `cell_texts` reads off a panel showing `shot` with its tiles in `layout`: each
    cell as its own kind, and a spin cell also as a decimal with the point put in."""
    cells = []
    for field in layout:
        texts = dict.fromkeys(KINDS)
        texts[KIND[field]] = shot[field]
        if KIND[field] == "spin":
            texts["decimal"] = f"{shot[field][:-1]}.{shot[field][-1]}"
        cells.append(texts)
    return cells


def _moved(a: str, b: str) -> tuple[str, ...]:
    """The shipped layout with two tiles traded."""
    layout = list(FIELDS)
    i, j = layout.index(a), layout.index(b)
    layout[i], layout[j] = layout[j], layout[i]
    return tuple(layout)


def test_a_shot_off_the_screen_breaks_no_check():
    for shot in SHOTS:
        assert broken(shot) == (set(), 5)


@pytest.mark.parametrize(
    ("change", "fails"),
    [
        ({"ball_speed": "82.8", "club_speed": "106.9"}, {"smash", "ball_speed", "club_speed"}),
        ({"smash": "1.39"}, {"smash", "ball_speed", "club_speed"}),
        ({"face_to_target": "2.0 O"}, {"face_to_target", "club_path", "face_to_path"}),
        ({"back_spin": "6247"}, {"total_spin", "back_spin"}),
        ({"carry_raw": "64.3"}, {"carry_game", "carry_raw"}),
        ({"total_length": "64.3"}, {"total_length", "carry_game"}),
    ],
)
def test_numbers_that_contradict_each_other_break_their_check(change, fails):
    assert broken(SHOTS[0] | change)[0] == fails


def test_a_check_with_a_blank_field_does_not_run():
    assert broken(SHOTS[0] | {"club_speed": None, "back_spin": None}) == (set(), 3)


def test_the_shipped_layout_is_told_from_its_numbers():
    assert solve_layout([_texts(FIELDS, shot) for shot in SHOTS]) == FIELDS


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ball_speed", "club_speed"),
        ("peak_height", "total_length"),
        ("face_to_path", "face_to_target"),
        ("back_spin", "total_spin"),
        ("club_path", "smash"),
        ("carry_raw", "club_speed"),
        ("peak_height", "back_spin"),
    ],
)
def test_moved_tiles_are_told_from_the_numbers(a, b):
    layout = _moved(a, b)
    assert solve_layout([_texts(layout, shot) for shot in SHOTS]) == layout


def test_the_two_carries_keep_their_shipped_order():
    layout = _moved("carry_game", "carry_raw")
    assert solve_layout([_texts(layout, shot) for shot in SHOTS]) == FIELDS


def test_one_frame_does_not_settle_a_layout():
    assert solve_layout([_texts(FIELDS, SHOTS[0])]) is None
    assert solve_layout([]) is None


def test_a_cell_that_does_not_read_leaves_the_layout_unsettled():
    samples = [_texts(FIELDS, shot) for shot in SHOTS]
    for sample in samples:
        sample[3] = dict.fromkeys(KINDS)
    assert solve_layout(samples) is None


def test_a_panel_with_another_set_of_tiles_is_not_settled():
    samples = [_texts(FIELDS, shot) for shot in SHOTS]
    for sample in samples:
        sample[8] = sample[4]  # a third spin where peak height was
    assert solve_layout(samples) is None


def test_a_misread_frame_does_not_unsettle_the_layout():
    good = [_texts(FIELDS, shot) for shot in SHOTS] * 2
    bad = _texts(FIELDS, SHOTS[0] | {"smash": "1.99"})
    assert solve_layout([*good, bad]) == FIELDS


def test_the_shipped_layout_stands_in_only_when_the_numbers_allow_it():
    one_frame = [_texts(FIELDS, SHOTS[0])]
    assert solve_layout(one_frame) is None and shipped_holds(one_frame)
    moved = [_texts(_moved("ball_speed", "peak_height"), SHOTS[0])]
    assert solve_layout(moved) is None and not shipped_holds(moved)
    assert shipped_holds([])  # nothing read says nothing against it


def _traded(rgb: np.ndarray, box, a: str, b: str) -> np.ndarray:
    """The frame with the whole cells of two fields traded, titles and all."""
    out = rgb.copy()
    x0, x1, y0, y1 = box
    w, h = (x1 - x0) // 2, (y1 - y0) // 6

    def cell(field):
        row, col = divmod(FIELDS.index(field), 2)
        return slice(y0 + row * h, y0 + (row + 1) * h), slice(x0 + col * w, x0 + (col + 1) * w)

    out[cell(a)], out[cell(b)] = rgb[cell(b)], rgb[cell(a)]
    return out


def test_tiles_traded_in_a_frame_of_the_screen_are_told_apart():
    glyphs = Glyphs.load(PANEL_REFERENCE.with_name("sim_glyphs.npz"))
    rgb, labels = load("4877-both")
    panel = find_panel(rgb)
    assert solve_layout([cell_texts(rgb, panel.box, glyphs)] * 2) == FIELDS
    moved = _moved("ball_speed", "club_speed")
    texts = cell_texts(_traded(rgb, panel.box, "ball_speed", "club_speed"), panel.box, glyphs)
    cells = dict(zip(moved, texts, strict=True))
    assert cells["ball_speed"]["decimal"] == labels["ball_speed"]
    assert cells["club_speed"]["decimal"] == labels["club_speed"]
    assert len(value_boxes(panel.box)) == len(moved)
    # This shot's club speed over its peak height is its smash too, so it takes a second
    # shot to tell the traded tiles from ball speed staying put.
    assert solve_layout([texts] * 2) is None
    assert solve_layout([texts, texts, _texts(moved, SHOTS[1])]) == moved
