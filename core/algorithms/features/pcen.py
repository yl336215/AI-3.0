"""Per-channel energy normalization feature extraction."""

import librosa
import numpy as np


def extract(data: np.ndarray, sample_rate: float, n_fft: int = 256, hop_length: int = 128, n_mels: int = 13, time_constant: float = 0.06) -> np.ndarray:
    mel = librosa.feature.melspectrogram(y=data, sr=sample_rate, n_mels=n_mels, fmax=sample_rate / 2, n_fft=n_fft, hop_length=hop_length, power=2.0)
    values = librosa.pcen(mel * (2 ** 31), sr=sample_rate, hop_length=hop_length, time_constant=time_constant, gain=0.8, bias=10.0, power=0.25, eps=1e-6)
    scale = max(float(np.percentile(values, 99.5)), 1e-6)
    return np.clip(values / scale, 0.0, 1.0)
