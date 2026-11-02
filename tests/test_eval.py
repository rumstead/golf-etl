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


def test_a_no_ball_motion_is_neither_found_nor_false():
    s = score([5.0, 9.0, 30.0], [5.0], tolerance_s=0.15, no_ball=[9.05, 20.0])
    assert (s.true_pos, s.false_pos, s.false_neg) == (1, 1, 0)


def test_a_strike_is_matched_before_a_no_ball_motion_next_to_it():
    s = score([10.0], [10.1], tolerance_s=0.15, no_ball=[9.95])
    assert (s.true_pos, s.false_pos, s.false_neg) == (1, 0, 0)


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


def test_a_video_can_override_the_tolerance(tmp_path):
    (tmp_path / "labels.yaml").write_text(
        "tolerance_ms: 17\n"
        "videos:\n"
        "  - path: exact.mov\n    impacts: [5.0]\n"
        "  - path: rough.mov\n    tolerance_ms: 100\n    impacts: [5.0]\n"
    )
    total, _ = run(tmp_path / "labels.yaml", Settings(), lambda video, cfg: [5.05])
    assert (total.true_pos, total.false_neg) == (1, 1)  # only rough.mov allows 50ms


def test_run_reads_a_videos_no_ball_motions(tmp_path):
    (tmp_path / "labels.yaml").write_text(
        "tolerance_ms: 100\nvideos:\n  - path: a.mov\n    impacts: [5.0]\n    no_ball: [9.0]\n"
    )
    total, _ = run(tmp_path / "labels.yaml", Settings(), lambda video, cfg: [5.0, 9.02])
    assert (total.true_pos, total.false_pos, total.false_neg) == (1, 0, 0)
