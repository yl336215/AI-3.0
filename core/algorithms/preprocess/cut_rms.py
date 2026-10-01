"""Trim signal edges and normalize the remaining signal by RMS."""

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class CutRmsResult:
    values: np.ndarray
    start_s: float
    end_s: float


def apply(data: np.ndarray, sample_rate: float, trim_seconds: float = 0.5, target_rms: float = 0.3) -> CutRmsResult:
    trim_points = max(0, round(sample_rate * trim_seconds))
    can_trim = trim_points > 0 and data.size > 2 * trim_points
    values = data[trim_points:-trim_points].copy() if can_trim else data.copy()
    start_s = trim_points / sample_rate if can_trim else 0.0
    rms = float(np.sqrt(np.mean(values.astype(np.float64) ** 2))) if values.size else 0.0
    if np.isfinite(rms) and rms > 0:
        values *= target_rms / rms
    return CutRmsResult(values=values, start_s=start_s, end_s=start_s + values.size / sample_rate)
