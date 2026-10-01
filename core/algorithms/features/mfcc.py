"""Mel-frequency cepstral coefficient extraction."""

import librosa
import numpy as np


def extract(data: np.ndarray, sample_rate: float, count: int = 13, n_fft: int = 256, hop_length: int = 128) -> np.ndarray:
    return librosa.feature.mfcc(y=data, sr=sample_rate, n_mfcc=count, n_fft=n_fft, hop_length=hop_length, fmax=sample_rate / 2)
