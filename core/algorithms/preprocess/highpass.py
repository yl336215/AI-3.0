"""Zero-phase high-pass filtering for waveform inspection."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfiltfilt


def apply(values: np.ndarray, sampling_rate_hz: float, cutoff_hz: float = 20.0) -> np.ndarray:
    signal = np.asarray(values, dtype=np.float64)
    if signal.size == 0:
        return signal.copy()
    if sampling_rate_hz <= 2 * cutoff_hz:
        raise ValueError("采样率必须高于滤波截止频率的两倍")
    sections = butter(4, cutoff_hz, btype="highpass", fs=sampling_rate_hz, output="sos")
    try:
        return sosfiltfilt(sections, signal)
    except ValueError:
        # Very short clips cannot provide enough padding for zero-phase filtering.
        return sosfilt(sections, signal)
