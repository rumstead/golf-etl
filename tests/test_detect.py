import numpy as np
import pytest

from golf_etl.audio import Onset
from golf_etl.config import Settings
from golf_etl.detect import confirm, detect_swings
from golf_etl.pose import LEFT_ANKLE, LEFT_WRIST, RIGHT_ANKLE, RIGHT_WRIST
from tests.poses import (
    empty_track,
    fast_finish_track,
    scaled,
    session_track,
    still_track,
    swing_track,
    waggle_track,
    windows,
)

CFG = Settings()


def test_confirms_swing_with_peak_at_the_sound():
    check = confirm(swing_track(10.0), 10.0, CFG)
    assert check.reason == ""
    assert abs(check.offset_ms) <= CFG.confirm_window_ms


def test_rejects_a_sound_while_the_hands_are_slow():
    # a club tap or a neighbor's impact 1s before the golfer's own downswing
    check = confirm(swing_track(11.0), 10.0, CFG)
    assert check.offset_ms is None
    assert check.reason.startswith("hands too slow")


def test_confirms_when_the_follow_through_is_faster_than_impact():
    track = fast_finish_track(10.0)
    speed = track.hand_speed()
    assert track.times[int(np.nanargmax(speed))] > 10.5  # the fastest moment is the finish
    check = confirm(track, 10.0, CFG)
    assert check.reason == ""
    assert abs(check.offset_ms) <= CFG.confirm_window_ms


def test_rejects_still_person_and_no_pose():
    assert confirm(still_track(10.0), 10.0, CFG).reason.startswith("hands too slow")
    assert confirm(empty_track(10.0), 10.0, CFG).reason == "no pose found"


def test_hand_speed_does_not_depend_on_how_close_the_phone_is():
    far = confirm(swing_track(10.0), 10.0, CFG)
    close = confirm(scaled(swing_track(10.0), 1.6), 10.0, CFG)
    assert far.reason == close.reason == ""
    assert close.speed == pytest.approx(far.speed, rel=0.01)


def test_rejects_fast_hands_at_the_finish():
    check = confirm(fast_finish_track(10.0), 10.75, CFG)
    assert check.offset_ms is None
    assert check.reason.startswith("hands above the hips")


def test_rejects_a_fast_waggle_with_the_hands_low():
    check = confirm(waggle_track(10.0, hand_y=0.75), 10.0, CFG)
    assert check.offset_ms is None
    assert check.reason.startswith("hands below the hips")


def test_rejects_fast_hands_that_start_well_after_the_sound():
    # walking off with the club, or a waggle, a quarter second after a tap
    track = still_track(10.0)
    late = track.times > 10.2
    rise = 0.6 - 2.0 * (track.times[late] - 10.2)
    track.landmarks[late, LEFT_WRIST, 1] = rise
    track.landmarks[late, RIGHT_WRIST, 1] = rise
    assert confirm(track, 10.0, CFG).reason.startswith("hands too slow")


def test_rejects_when_the_feet_are_out_of_frame():
    track = swing_track(10.0)
    track.landmarks[:, [LEFT_ANKLE, RIGHT_ANKLE]] = np.nan
    assert confirm(track, 10.0, CFG).reason == "legs not in frame"


def test_detect_splits_swings_and_rejections_in_time_order():
    track_for = windows(session_track([5.0, 25.0], 40))
    onsets = [Onset(5.0, 1.0), Onset(14.0, 0.4), Onset(25.0, 0.9)]
    result = detect_swings(onsets, track_for, duration_s=600, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [5.0, 25.0]
    assert [r.time_s for r in result.rejected] == [14.0]


def test_a_louder_sound_that_fails_confirmation_does_not_hide_a_swing():
    # the finish or a club tap a few seconds after the strike, louder than the strike
    track_for = windows(session_track([10.0], 30))
    result = detect_swings([Onset(10.0, 0.3), Onset(12.5, 0.9)], track_for, 600, CFG)
    assert [s.impact_s for s in result.swings] == [10.0]
    assert [r.time_s for r in result.rejected] == [12.5]
    assert result.rejected[0].reason.startswith("hands too slow")


def test_keeps_the_faster_of_two_swings_within_the_gap():
    track_for = windows(session_track([10.0, 15.0], 30, down_s={15.0: 0.25}))
    result = detect_swings([Onset(10.0, 0.9), Onset(15.0, 0.4)], track_for, 600, CFG)
    assert [s.impact_s for s in result.swings] == [15.0]
    assert [(r.time_s, r.reason) for r in result.rejected] == [
        (10.0, "within 8s of a faster swing")
    ]


def test_only_kept_swings_get_the_full_clip_window():
    track_for = windows(session_track([10.0], 40))
    asked: list[tuple[float, float]] = []

    def recording(start: float, end: float):
        asked.append((start, end))
        return track_for(start, end)

    result = detect_swings([Onset(10.0, 0.9), Onset(30.0, 0.5)], recording, 600, CFG)
    full = (10.0 - CFG.pre_impact_s, 10.0 + CFG.post_impact_s)
    assert [w for w in asked if w[1] - w[0] > 1.0] == [full]
    swing = result.swings[0]
    assert swing.track.times[0] == pytest.approx(full[0], abs=1 / 60)
    assert swing.track.times[-1] == pytest.approx(full[1], abs=2 / 60)


def test_short_clip_keeps_only_the_fastest_swing():
    track_for = windows(session_track([2.6, 11.0], 14, down_s={11.0: 0.25}))
    onsets = [Onset(2.6, 1.0), Onset(11.0, 0.7)]
    result = detect_swings(onsets, track_for, duration_s=14, cfg=CFG)
    assert [s.impact_s for s in result.swings] == [11.0]
    assert [(r.time_s, r.reason) for r in result.rejected] == [
        (2.6, "single-swing clip keeps the fastest")
    ]


def test_no_onsets_is_empty_not_an_error():
    result = detect_swings([], lambda start, end: pytest.fail("no tracks needed"), 30, CFG)
    assert result.swings == [] and result.rejected == []
