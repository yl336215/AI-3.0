"""Mel power spectrogram used by the annotation overview."""

from dataclasses import dataclass
import librosa
import numpy as np


@dataclass(frozen=True)
class MelSpectrogram:
    decibels: np.ndarray
    times_s: np.ndarray
    frequencies_hz: np.ndarray


def extract(data: np.ndarray, sample_rate: float, n_fft: int = 256, n_mels: int = 39) -> MelSpectrogram:
    hop_length = n_fft // 2
    power = librosa.feature.melspectrogram(
        y=data,
        sr=sample_rate,
        n_mels=n_mels,
        fmax=sample_rate / 2.0,
        n_fft=n_fft,
        hop_length=hop_length,
    )
    decibels = librosa.power_to_db(power, ref=np.max)
    times = librosa.frames_to_time(np.arange(decibels.shape[1]), sr=sample_rate, hop_length=hop_length)
    frequencies = librosa.mel_frequencies(n_mels=n_mels, fmax=sample_rate / 2.0)
    return MelSpectrogram(decibels=decibels, times_s=times, frequencies_hz=frequencies)
