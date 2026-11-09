from pathlib import Path

import numpy as np
import pytest

from golf_etl.sim import FIELDS, Panel
from golf_etl.sim_eval import LabeledShot, held_out, labeled_cells, sits_out_glyphs


def _shot(video: str, glyph_source: bool) -> LabeledShot:
    crop = np.zeros((243, 126, 3), np.uint8)
    texts = {f: "1.0" for f in FIELDS[:2]}
    panel = Panel((0, 126, 0, 243), 0.7)
    return LabeledShot(video, 10.0, texts, panel, [(13.0, crop)], glyph_source)


def test_labeled_cells_leave_out_a_video_that_sits_out_the_glyph_build(monkeypatch):
    shots = (_shot("old.MOV", True), _shot("new.mov", False))
    monkeypatch.setattr("golf_etl.sim_eval._shots", lambda path: shots)
    videos = {video for video, _, _, _ in labeled_cells(Path("labels.yaml"))}
    assert videos == {"old.MOV"}


def test_a_video_sits_out_the_glyph_build_only_when_its_entry_says_so():
    assert sits_out_glyphs({"path": "a.mov", "glyphs": False})
    assert not sits_out_glyphs({"path": "a.mov", "glyphs": True})
    assert not sits_out_glyphs({"path": "a.mov"})


def test_a_glyphs_flag_that_is_not_a_bool_is_an_error():
    with pytest.raises(ValueError, match="a.mov: glyphs must be true or false"):
        sits_out_glyphs({"path": "a.mov", "glyphs": "false"})


def test_held_out_names_a_video_whose_glyph_pool_is_empty(monkeypatch):
    shots = (_shot("only.MOV", True), _shot("new.mov", False))
    monkeypatch.setattr("golf_etl.sim_eval._shots", lambda path: shots)
    glyphs_for = held_out(Path("labels.yaml"))
    with pytest.raises(ValueError, match="only.MOV: no other video to build glyphs from"):
        glyphs_for("only.MOV")
