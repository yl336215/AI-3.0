"""Mel spectrogram with a separate spectral-energy overview."""

from typing import Any
import librosa
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

CARD_ID = "mel_detail"
SPEC = {"title": "Mel 详情", "params": [
    {"key": "n_mels", "label": "Mel 频带数（频率分辨率）", "type": "number", "default": 39, "min": 8, "max": 128, "step": 1},
]}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    bands = max(8, min(128, int(params.get("n_mels") or 39)))
    hop = 128
    mel = librosa.feature.melspectrogram(y=data, sr=sample_rate, n_mels=bands, fmax=sample_rate / 2, n_fft=256, hop_length=hop)
    decibels = librosa.power_to_db(mel, ref=np.max)
    times = np.arange(decibels.shape[1]) * hop / sample_rate
    figure = make_subplots(rows=2, cols=1, row_heights=[0.25, 0.75], vertical_spacing=0.08, subplot_titles=["频谱能量", "Mel 频谱"])
    figure.add_trace(go.Scatter(x=times, y=np.mean(decibels, axis=0), mode="lines", line={"color": "red"}, showlegend=False), row=1, col=1)
    figure.add_trace(go.Heatmap(z=decibels, x=times, y=librosa.mel_frequencies(n_mels=bands, fmax=sample_rate / 2), colorscale="Plasma", colorbar={"title": "功率（dB）"}, showscale=True), row=2, col=1)
    figure.update_layout(height=760, showlegend=False, margin={"l": 45, "r": 30, "t": 45, "b": 35})
    figure.update_xaxes(title_text="时间（秒）", row=2, col=1)
    figure.update_yaxes(title_text="频率（Hz）", row=2, col=1)
    return figure
