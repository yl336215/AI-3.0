"""Discrete wavelet decomposition curves."""

from typing import Any
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pywt

CARD_ID = "dwt"
SPEC = {"title": "小波分解 DWT", "params": [
    {"key": "wavelet", "label": "小波基", "type": "select", "default": "db22", "options": ["db4", "db8", "db22", "sym8", "coif5"]},
    {"key": "level", "label": "分解层数", "type": "number", "default": 4, "min": 1, "max": 8, "step": 1},
]}


def build(data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> go.Figure:
    wavelet = str(params.get("wavelet") or "db22")
    level = max(1, min(8, int(params.get("level") or 4)))
    coefficients = pywt.wavedec(data, wavelet, level=level)
    titles = []
    for index in range(len(coefficients)):
        band = level if index == 0 else level - index + 1
        name = f"A{level}" if index == 0 else f"D{band}"
        low = 0.0 if index == 0 else sample_rate / (2 ** (band + 1))
        high = sample_rate / (2 ** (band + 1)) if index == 0 else sample_rate / (2 ** band)
        titles.append(f"{name} 特征（{low:.1f}-{high:.1f} Hz）")
    figure = make_subplots(rows=len(coefficients), cols=1, vertical_spacing=0.03, subplot_titles=titles)
    for row, coefficient in enumerate(coefficients, start=1):
        # 峰值和局部峰度数字标注已停用；仅绘制各层分解曲线。
        figure.add_trace(go.Scatter(y=coefficient, mode="lines", line={"color": "blue", "width": 1}, showlegend=False), row=row, col=1)
    figure.update_layout(height=max(460, len(coefficients) * 260), template="plotly_white", margin={"l": 40, "r": 20, "t": 45, "b": 30})
    return figure
