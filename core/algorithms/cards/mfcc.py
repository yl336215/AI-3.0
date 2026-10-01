"""Mel-frequency cepstral coefficient detail view."""

from typing import Any
import numpy as np
import plotly.graph_objects as go

from core.algorithms.features.mfcc import extract

CARD_ID = "mfcc"
SPEC = {"title": "MFCC 详情", "params": [
    {"key": "n_mfcc", "label": "MFCC 系数数", "type": "number", "default": 13, "min": 4, "max": 40, "step": 1},
]}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    count = max(4, min(40, int(params.get("n_mfcc") or 13)))
    values = extract(data, sample_rate, count=count)
    heatmap = go.Heatmap(z=values, x=np.arange(values.shape[1]) * 128 / sample_rate, y=np.arange(1, count + 1), colorscale="Viridis", colorbar={"title": "MFCC 系数"})
    return go.Figure(heatmap).update_layout(xaxis_title="时间（秒）", yaxis_title="MFCC 序号", height=460)
