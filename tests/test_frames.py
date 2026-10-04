import cv2
import numpy as np
import pytest

from golf_etl.frames import pick_positions, sharpest
from tests.poses import swing_track


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
    assert dict(seq)["transition"] == pytest.approx((p.top_s + p.impact_s) / 2)
    assert dict(seq)["takeaway"] == pytest.approx(p.address_s + (p.top_s - p.address_s) / 3)
