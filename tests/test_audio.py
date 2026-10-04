import numpy as np
import pytest

from golf_etl.audio import find_onsets

SR = 48000


def track(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 0.005).astype(np.float32)


def add_click(y: np.ndarray, at_s: float, amp: float, seed: int = 1) -> None:
    """Broadband impact: 20ms of noise with a fast decay."""
    rng = np.random.default_rng(seed)
    n = int(0.02 * SR)
    burst = rng.standard_normal(n) * np.exp(-np.linspace(0, 8, n)) * amp
    i = int(at_s * SR)
    y[i : i + n] += burst.astype(np.float32)


def add_thump(y: np.ndarray, at_s: float, amp: float) -> None:
    """Low-frequency hit (a bag dropped, a footstep) that the high-pass should remove."""
    n = int(0.08 * SR)
    t = np.arange(n) / SR
    i = int(at_s * SR)
    y[i : i + n] += (np.sin(2 * np.pi * 120 * t) * np.exp(-t * 30) * amp).astype(np.float32)


def detect(y, **kw):
    args = {"highpass_hz": 2000.0, "delta": 0.2, "min_gap_s": 8.0, "lag_s": 0.0} | kw
    return find_onsets(y, SR, **args).kept


def test_finds_each_impact():
    y = track(30)
    for at in (5.0, 15.0, 25.0):
        add_click(y, at, 0.8)
    times = [o.time_s for o in detect(y)]
    assert times == pytest.approx([5.0, 15.0, 25.0], abs=0.03)


def test_weaker_onset_within_min_gap_is_dropped():
    y = track(30)
    add_click(y, 10.0, 0.8)
    add_click(y, 13.0, 0.3, seed=2)
    result = find_onsets(y, SR, highpass_hz=2000.0, delta=0.2, min_gap_s=8.0)
    assert [o.time_s for o in result.kept] == pytest.approx([10.0], abs=0.03)
    assert result.kept[0].strength == pytest.approx(1.0)
    assert [o.time_s for o in result.suppressed] == pytest.approx([13.0], abs=0.03)


def test_low_frequency_thump_is_ignored():
    y = track(20)
    add_click(y, 4.0, 0.8)
    add_thump(y, 14.0, 0.9)
    assert [o.time_s for o in detect(y)] == pytest.approx([4.0], abs=0.03)


def test_quieter_neighbor_far_away_is_kept_with_lower_strength():
    y = track(30)
    add_click(y, 5.0, 0.8)
    add_click(y, 20.0, 0.3, seed=3)
    onsets = detect(y)
    assert [o.time_s for o in onsets] == pytest.approx([5.0, 20.0], abs=0.03)
    assert onsets[1].strength < onsets[0].strength


def test_silence_and_tiny_input_return_nothing():
    assert detect(np.zeros(SR * 5, dtype=np.float32)) == []
    assert detect(np.zeros(10, dtype=np.float32)) == []


def test_times_start_at_the_transient_and_subtract_the_lag():
    y = track(10)
    add_click(y, 5.0, 0.8)
    assert detect(y)[0].time_s == pytest.approx(5.0, abs=0.01)  # within a 120fps frame
    shifted = detect(y, lag_s=0.012)[0].time_s
    assert shifted == pytest.approx(detect(y)[0].time_s - 0.012)


def test_long_session_stays_inside_a_memory_budget():
    import tracemalloc

    y = track(600)  # a 10 minute range session
    clicks = [30.0 + 60.0 * i for i in range(10)]
    for i, at in enumerate(clicks):
        add_click(y, at, 0.8, seed=i + 10)
    tracemalloc.start()
    found = detect(y)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert [o.time_s for o in found] == pytest.approx(clicks, abs=0.03)
    assert peak < 600 * 1024**2


def test_chunk_boundaries_do_not_move_or_drop_onsets():
    y = track(40)
    for i, at in enumerate((4.0, 13.0, 19.9, 31.0)):  # 19.9 sits just before a 10s boundary
        add_click(y, at, 0.8 - 0.1 * i, seed=i + 20)
    args = {"highpass_hz": 2000.0, "delta": 0.2, "min_gap_s": 5.0}
    whole = find_onsets(y, SR, chunk_s=120.0, **args).kept
    chunked = find_onsets(y, SR, chunk_s=10.0, **args).kept
    assert [o.time_s for o in chunked] == pytest.approx([o.time_s for o in whole], abs=0.003)
    assert [o.strength for o in chunked] == pytest.approx([o.strength for o in whole], abs=0.05)
