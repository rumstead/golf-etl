"""Impact candidates from audio: sharp high-frequency transients."""

from dataclasses import dataclass

import librosa
import numpy as np
from scipy import signal

SAMPLE_RATE = 48000  # iPhone audio is 48kHz; downsampling smears the transient
HOP = 128
CHUNK_S = 30.0  # the envelope is built in chunks so a 20 minute session fits in memory
OVERLAP_S = 1.0


@dataclass(frozen=True)
class Onset:
    time_s: float  # start of the transient, minus the audio lag
    strength: float  # 0..1, relative to the strongest onset in the video


@dataclass(frozen=True)
class Onsets:
    kept: list[Onset]
    suppressed: list[Onset]  # within min_gap_s of a stronger onset


def find_onsets(
    samples: np.ndarray,
    sr: int,
    highpass_hz: float,
    delta: float,
    min_gap_s: float,
    lag_s: float = 0.0,
    chunk_s: float = CHUNK_S,
) -> Onsets:
    """Onsets in time order, keeping only the strongest within any min_gap_s span.

    Times are backtracked from the envelope peak to where the transient starts, then moved
    earlier by lag_s (sound travel to the phone plus the phone's own audio/video offset).
    """
    if samples.size < sr // 10:
        return Onsets([], [])
    env = _envelope(samples, sr, highpass_hz, chunk_s)
    peak = float(env.max())
    if peak <= 0:
        return Onsets([], [])
    env = env / peak
    peaks = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=HOP, units="frames", normalize=False, delta=delta
    )
    starts = librosa.onset.onset_backtrack(peaks, env)
    times = librosa.frames_to_time(starts, sr=sr, hop_length=HOP) - lag_s
    candidates = sorted(
        (Onset(max(0.0, float(t)), float(env[p])) for t, p in zip(times, peaks, strict=True)),
        key=lambda o: o.strength,
        reverse=True,
    )
    kept: list[Onset] = []
    suppressed: list[Onset] = []
    for onset in candidates:
        if all(abs(onset.time_s - k.time_s) >= min_gap_s for k in kept):
            kept.append(onset)
        else:
            suppressed.append(onset)
    return Onsets(sorted(kept, key=lambda o: o.time_s), sorted(suppressed, key=lambda o: o.time_s))


def _envelope(samples: np.ndarray, sr: int, highpass_hz: float, chunk_s: float) -> np.ndarray:
    """Onset strength of the high-passed signal, one value per HOP samples.

    Each chunk is filtered and analyzed with OVERLAP_S of context on both sides, then trimmed,
    so frame i always lines up with sample i * HOP no matter where the chunk edges fall.
    """
    sos = signal.butter(4, highpass_hz, btype="highpass", fs=sr, output="sos")
    chunk = max(HOP, int(chunk_s * sr) // HOP * HOP)
    pad = int(OVERLAP_S * sr) // HOP * HOP
    parts = []
    for start in range(0, len(samples), chunk):
        lo, hi = max(0, start - pad), min(len(samples), start + chunk + pad)
        seg = signal.sosfiltfilt(sos, samples[lo:hi]).astype(np.float32)
        env = librosa.onset.onset_strength(y=seg, sr=sr, hop_length=HOP)
        first = (start - lo) // HOP
        last = None if start + chunk >= len(samples) else first + chunk // HOP
        parts.append(env[first:last])
    return np.concatenate(parts)
