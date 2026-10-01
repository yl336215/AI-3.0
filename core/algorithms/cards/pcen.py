"""Per-channel energy normalization for impact enhancement."""

from typing import Any
import numpy as np
import plotly.graph_objects as go
import librosa

from core.algorithms.features.pcen import extract

CARD_ID = "pcen"
SPEC = {"title": "PCEN 冲击增强", "params": [
    {"key": "n_fft", "label": "n_fft（频率分辨率）", "type": "select", "default": 256, "options": [128, 256, 512, 1024, 2048]},
    {"key": "hop_length", "label": "hop（时间分辨率）", "type": "number", "default": 128, "min": 16, "max": 1024, "step": 16},
    {"key": "n_mels", "label": "Mel 频带数", "type": "number", "default": 13, "min": 8, "max": 128, "step": 1},
    {"key": "time_constant", "label": "time_constant", "type": "number", "default": 0.06, "min": 0.01, "max": 1.0, "step": 0.01},
]}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    n_fft = int(params.get("n_fft") or 256)
    hop = int(params.get("hop_length") or 128)
    bands = int(params.get("n_mels") or 13)
    time_constant = float(params.get("time_constant") or 0.06)
    shown = extract(data, sample_rate, n_fft=n_fft, hop_length=hop, n_mels=bands, time_constant=time_constant)
    heatmap = go.Heatmap(z=shown, x=np.arange(shown.shape[1]) * hop / sample_rate, y=librosa.mel_frequencies(n_mels=bands, fmax=sample_rate / 2), colorscale="Plasma", colorbar={"title": "PCEN"}, zmin=0, zmax=1)
    return go.Figure(heatmap).update_layout(xaxis_title="时间（秒）", yaxis_title="频率（Hz）", height=460)
