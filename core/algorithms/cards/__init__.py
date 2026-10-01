"""Registry for standalone annotation analysis algorithms."""

from __future__ import annotations

import json
from typing import Any, Callable

import numpy as np
import plotly.graph_objects as go

from . import dwt, emd, mel, mfcc, pcen, wavelet

CardBuilder = Callable[[np.ndarray, float, dict[str, Any]], go.Figure]
_MODULES = (dwt, pcen, mfcc, mel, wavelet, emd)
CARD_SPECS = {module.CARD_ID: module.SPEC for module in _MODULES}
_BUILDERS: dict[str, CardBuilder] = {module.CARD_ID: module.build for module in _MODULES}


def build_analysis(card_id: str, data: np.ndarray, sample_rate: float, params: dict[str, Any]) -> dict[str, Any]:
    builder = _BUILDERS.get(card_id)
    if builder is None:
        raise ValueError(f"不支持的分析方法：{card_id}")
    return json.loads(builder(data, sample_rate, params).to_json())


__all__ = ["CARD_SPECS", "build_analysis"]
