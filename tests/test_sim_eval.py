from golf_etl.sim import FIELDS, Panel
from golf_etl.sim_eval import LabeledShot, labeled_cells, sits_out_glyphs
from tests.sim_fixtures import load


def _shot(video: str, glyph_source: bool) -> LabeledShot:
    rgb, labels = load("4761-s1")
    crop = rgb[40 : 40 + 243, 40 : 40 + 126]
    panel = Panel((0, 126, 0, 243), 0.7)
    texts = {f: labels[f] for f in FIELDS if labels[f]}
    return LabeledShot(video, 10.0, texts, panel, [(13.0, crop)], glyph_source)


def test_labeled_cells_leave_out_a_video_that_sits_out_the_glyph_build(tmp_path, monkeypatch):
    shots = (_shot("old.MOV", True), _shot("new.mov", False))
    monkeypatch.setattr("golf_etl.sim_eval._shots", lambda path: shots)
    labels = tmp_path / "labels.yaml"
    labels.write_text("videos: []\n")
    videos = {video for video, _, _, _ in labeled_cells(labels)}
    assert videos == {"old.MOV"}


def test_a_video_sits_out_the_glyph_build_only_when_its_entry_says_so():
    assert sits_out_glyphs({"path": "a.mov", "glyphs": False})
    assert not sits_out_glyphs({"path": "a.mov", "glyphs": True})
    assert not sits_out_glyphs({"path": "a.mov"})


def test_a_labeled_shot_is_a_glyph_source_by_default():
    assert LabeledShot("a.mov", 1.0, {}, None, []).glyph_source is True
