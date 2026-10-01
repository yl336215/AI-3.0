"""Empirical mode decomposition into intrinsic mode functions."""

from typing import Any
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from PyEMD import EMD

CARD_ID = "emd"
SPEC = {"title": "EMD 详情", "params": []}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    del sample_rate, params
    components = EMD()(data, max_imf=3)
    residual = data - np.sum(components, axis=0)
    titles = [f"本征模态 {index + 1}" for index in range(components.shape[0])] + ["残差"]
    figure = make_subplots(rows=components.shape[0] + 1, cols=1, vertical_spacing=0.06, subplot_titles=titles)
    for row, component in enumerate(components, start=1):
        figure.add_trace(go.Scatter(y=component, mode="lines", line={"width": 1}, showlegend=False), row=row, col=1)
    figure.add_trace(go.Scatter(y=residual, mode="lines", line={"width": 1, "color": "#FF7F0E"}, showlegend=False), row=components.shape[0] + 1, col=1)
    figure.update_layout(height=800, template="plotly_white", margin={"l": 40, "r": 20, "t": 45, "b": 30})
    return figure
