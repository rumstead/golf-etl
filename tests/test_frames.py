import cv2
import numpy as np
import pytest

from golf_etl.frames import after_shot_time, pick_positions, sharpest, swing_timing
from golf_etl.pose import (
    LEFT_HIP,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from tests.poses import late_rise_track, swing_track


def test_positions_on_a_clean_swing():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.top_s == pytest.approx(9.6, abs=0.05)
    assert 8.0 <= p.address_s <= 8.85  # still before takeaway at 8.8
    assert p.impact_s == 10.0
    assert p.finish_s == pytest.approx(10.8, abs=0.05)  # hands highest after impact


def test_positions_without_pose_fall_back_to_offsets():
    track = swing_track(10.0, missing=(7.0, 12.0))
    p = pick_positions(track, 10.0, still_speed=0.15)
    assert (p.address_s, p.top_s, p.finish_s) == pytest.approx((8.2, 9.2, track.times[-1]))
    assert (p.takeaway_s, p.halfway_s) == pytest.approx((8.2 + 1 / 3, 8.2 + 2 / 3))
    assert not p.top_in_frame


def test_takeaway_is_when_the_hands_reach_the_hips_going_back():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.takeaway_s == pytest.approx(8.9, abs=1 / 60)


def test_takeaway_waits_for_the_hands_to_rise_when_they_address_at_hip_height():
    track = swing_track(10.0)
    track.landmarks[:, [LEFT_HIP, RIGHT_HIP], 1] = 0.6  # legs 0.3 long, hands level with hips
    address = track.times - 10.0 <= -1.2
    jitter = np.where(np.arange(address.sum()) % 2 == 0, 0.008, -0.008)
    track.landmarks[address, LEFT_WRIST, 1] += jitter
    track.landmarks[address, RIGHT_WRIST, 1] += jitter
    p = pick_positions(track, 10.0, still_speed=0.15)
    # 0.1 leg lengths (0.03) above the jittered address, so 0.04 to 0.08s into the backswing
    assert p.takeaway_s == pytest.approx(8.86, abs=0.03)
    assert p.takeaway_start_s == pytest.approx(8.8, abs=0.05)


def test_halfway_back_is_when_the_hands_reach_the_shoulders():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.halfway_s == pytest.approx(9.4, abs=1 / 60)


def test_takeaway_matches_the_body_position_on_a_late_rising_backswing():
    track = late_rise_track(10.0)
    p = pick_positions(track, 10.0, still_speed=0.15)
    below = track.hands_below_hips()[int(np.argmin(np.abs(track.times - p.takeaway_s)))]
    assert below == pytest.approx(0.0, abs=0.01)


def test_positions_without_hips_or_shoulders_space_the_backswing_in_time():
    track = swing_track(10.0)
    track.landmarks[:, [LEFT_HIP, RIGHT_HIP, LEFT_SHOULDER, RIGHT_SHOULDER]] = np.nan
    p = pick_positions(track, 10.0, still_speed=0.15)
    back = p.top_s - p.address_s
    assert p.takeaway_s == pytest.approx(p.address_s + back / 3)
    assert p.halfway_s == pytest.approx(p.address_s + 2 * back / 3)


def test_transition_and_follow_through_sit_at_fixed_offsets_from_impact():
    p = pick_positions(
        swing_track(10.0), 10.0, still_speed=0.15, transition_s=0.1, follow_through_s=0.15
    )
    assert p.transition_s == pytest.approx(9.9)
    assert p.follow_through_s == pytest.approx(10.15)


def test_offsets_never_cross_the_top_or_the_finish():
    p = pick_positions(
        swing_track(10.0), 10.0, still_speed=0.15, transition_s=1.0, follow_through_s=5.0
    )
    assert p.transition_s == p.top_s
    assert p.follow_through_s == p.finish_s


def test_takeaway_start_is_where_the_hands_start_rising():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.takeaway_start_s == pytest.approx(8.8, abs=0.05)


def test_takeaway_start_ignores_jitter_at_address():
    # pose jitter on a close camera leaves no still stretch to end the address on
    track = swing_track(10.0)
    address = track.times - 10.0 <= -1.2
    jitter = np.where(np.arange(address.sum()) % 2 == 0, 0.008, -0.008)
    track.landmarks[address, LEFT_WRIST, 1] += jitter
    track.landmarks[address, RIGHT_WRIST, 1] += jitter
    p = pick_positions(track, 10.0, still_speed=0.15)
    assert p.takeaway_start_s == pytest.approx(8.8, abs=0.05)


def test_top_is_flagged_when_the_hands_leave_the_frame():
    track = swing_track(10.0)
    rel = track.times - 10.0
    out = (rel > -0.6) & (rel < -0.2)  # the hands go above the frame through the top
    track.landmarks[out, LEFT_WRIST, 2] = 0.2
    track.landmarks[out, RIGHT_WRIST, 2] = 0.3
    assert not pick_positions(track, 10.0, still_speed=0.15).top_in_frame
    assert pick_positions(swing_track(10.0), 10.0, still_speed=0.15).top_in_frame


def test_timing_counts_clip_frames_from_the_clip_start():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    t = swing_timing(p, clip_start_s=7.5, fps=120.0)
    assert t.takeaway_start_frame == round((p.takeaway_start_s - 7.5) * 120)
    assert t.top_frame == round((p.top_s - 7.5) * 120)
    assert t.impact_frame == 300
    assert t.backswing_s == pytest.approx(p.top_s - p.takeaway_start_s)
    assert t.downswing_s == pytest.approx(10.0 - p.top_s)
    assert t.tempo == pytest.approx(t.backswing_s / t.downswing_s)
    assert t.top_in_frame


def test_order_is_address_top_impact_finish():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.address_s < p.top_s < p.impact_s < p.finish_s


def test_sharpest_prefers_the_unblurred_frame():
    rng = np.random.default_rng(0)
    crisp = (rng.random((120, 160, 3)) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(crisp, (9, 9), 4)
    t, img = sharpest([(1.0, blurred), (1.1, crisp), (1.2, blurred)])
    assert t == 1.1 and img is crisp


def test_sequence_has_eight_positions_in_time_order():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    seq = p.sequence()
    assert [name for name, _ in seq] == [
        "address",
        "takeaway",
        "halfway back",
        "top",
        "transition",
        "impact",
        "follow-through",
        "finish",
    ]
    times = [t for _, t in seq]
    assert times == sorted(times)
    assert dict(seq)["takeaway"] == p.takeaway_s
    assert dict(seq)["transition"] == p.transition_s


def test_after_shot_time_defaults_to_the_delay():
    assert after_shot_time(10.0, 8.0, duration_s=600.0) == pytest.approx(18.0)


def test_after_shot_time_stops_before_the_next_swing_starts():
    # next impact at 20s; its clip window starts 2.5s earlier
    assert after_shot_time(10.0, 8.0, 600.0, next_impact_s=20.0, pre_impact_s=2.5) == 17.5


def test_after_shot_time_stays_inside_the_video():
    assert after_shot_time(10.0, 8.0, duration_s=12.0) == pytest.approx(11.95)


def test_after_shot_time_is_never_before_impact():
    assert after_shot_time(10.0, 8.0, duration_s=10.02) == 10.0
