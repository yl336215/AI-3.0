"""Mean short-time power spectrum in relative decibels."""

import librosa
import numpy as np


def extract(data: np.ndarray, sample_rate: float, n_fft: int = 256, hop_length: int = 128) -> tuple[np.ndarray, np.ndarray]:
    spectra = np.abs(librosa.stft(data, n_fft=n_fft, hop_length=hop_length)) ** 2
    decibels = 10.0 * np.log10(np.maximum(np.mean(spectra, axis=1), 1e-12))
    decibels -= float(np.max(decibels))
    return np.fft.rfftfreq(n_fft, 1.0 / sample_rate), decibels
