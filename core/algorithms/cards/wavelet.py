"""Continuous wavelet time-frequency detail view."""

from typing import Any
import numpy as np
import plotly.graph_objects as go
import pywt

CARD_ID = "wavelet"
SPEC = {"title": "Wavelet 详情", "params": []}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    del params
    target_frequencies = np.linspace(100, min(8000, sample_rate / 2), 25)
    scales = pywt.central_frequency("cmor1.5-1.0") * sample_rate / target_frequencies
    coefficients, frequencies = pywt.cwt(data, scales, "cmor1.5-1.0", sampling_period=1.0 / sample_rate)
    magnitude = np.abs(coefficients)
    magnitude = 20 * np.log10(magnitude / (np.max(magnitude) + 1e-6) + 1e-12)
    heatmap = go.Heatmap(z=magnitude, x=np.arange(magnitude.shape[1]) / sample_rate, y=frequencies, colorscale="Plasma", colorbar={"title": "功率（dB）"})
    return go.Figure(heatmap).update_layout(xaxis_title="时间（秒）", yaxis_title="频率（Hz）", height=460)
