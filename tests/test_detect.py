import pytest

from golf_etl.audio import Onset
from golf_etl.config import Settings
from golf_etl.detect import confirm, detect_swings
from tests.poses import empty_track, still_track, swing_track

CFG = Settings()


def test_confirms_swing_with_peak_at_the_sound():
    offset, reason = confirm(swing_track(10.0), 10.0, CFG)
    assert reason == ""
    assert abs(offset) <= CFG.confirm_window_ms


def test_rejects_when_peak_is_far_from_the_sound():
    # neighbor's impact 1s before the user's own downswing
    offset, reason = confirm(swing_track(11.0), 10.0, CFG)
    assert offset is None
    assert "from the sound" in reason


def test_rejects_still_person_and_no_pose():
    assert confirm(still_track(10.0), 10.0, CFG)[1].startswith("hands too slow")
    assert confirm(empty_track(10.0), 10.0, CFG) == (None, "no pose found")


def test_detect_splits_swings_and_rejections_in_time_order():
    tracks = {5.0: swing_track(5.0), 14.0: still_track(14.0), 25.0: swing_track(25.0)}
    onsets = [Onset(5.0, 1.0), Onset(14.0, 0.4), Onset(25.0, 0.9)]
    result = detect_swings(onsets, tracks.__getitem__, duration_s=600, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [5.0, 25.0]
    assert [r.time_s for r in result.rejected] == [14.0]


def test_short_clip_keeps_only_strongest_swing():
    tracks = {2.6: swing_track(2.6), 9.0: swing_track(9.0)}
    onsets = [Onset(2.6, 0.7), Onset(9.0, 1.0)]
    result = detect_swings(onsets, tracks.__getitem__, duration_s=12, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [9.0]
    assert result.rejected[0].reason == "single-swing clip keeps the strongest"


def test_no_onsets_is_empty_not_an_error():
    result = detect_swings([], lambda t: pytest.fail("no tracks needed"), 30, CFG)
    assert result.swings == [] and result.rejected == []


def test_loud_onset_dropped_by_the_gap_is_reported():
    loud, quiet = Onset(12.0, 0.9), Onset(13.0, 0.2)
    result = detect_swings(
        [Onset(5.0, 1.0)], lambda t: swing_track(t), 600, CFG, suppressed=[loud, quiet]
    )
    assert [(r.time_s, r.reason) for r in result.rejected] == [
        (12.0, "within 8s of a stronger sound")
    ]
