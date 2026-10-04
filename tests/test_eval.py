import pytest

from golf_etl.config import Settings
from golf_etl.eval import run, score


def test_perfect_detection():
    s = score([5.01, 15.0], [5.0, 15.0], tolerance_s=0.15)
    assert (s.true_pos, s.false_pos, s.false_neg) == (2, 0, 0)
    assert s.mean_error_ms == pytest.approx(5.0)


def test_misses_and_extras():
    s = score([5.0, 9.0, 30.0], [5.0, 15.0], tolerance_s=0.15)
    assert (s.true_pos, s.false_pos, s.false_neg) == (1, 2, 1)
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(0.5)


def test_one_prediction_cannot_match_two_impacts():
    s = score([10.0], [9.95, 10.05], tolerance_s=0.15)
    assert (s.true_pos, s.false_neg) == (1, 1)


def test_empty_inputs_are_perfect():
    s = score([], [], 0.15)
    assert s.precision == 1.0 and s.recall == 1.0


def test_run_reads_labels_relative_to_the_file(tmp_path):
    (tmp_path / "labels.yaml").write_text(
        "tolerance_ms: 100\nvideos:\n  - path: a.mov\n    impacts: [5.0, 15.0]\n"
    )
    seen = []

    def fake(video, cfg):
        seen.append(video)
        return [5.02]

    total, lines = run(tmp_path / "labels.yaml", Settings(), fake)
    assert seen == [tmp_path / "a.mov"]
    assert (total.true_pos, total.false_neg) == (1, 1)
    assert lines[-1].startswith("total: precision 1.00 recall 0.50")
