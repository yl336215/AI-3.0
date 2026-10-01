"""Advanced analysis accepts signal values without a source file."""

import numpy as np
import pytest
from fastapi import HTTPException

from web.api import main
from web.api.models import SignalAnalysisRequest


def test_analysis_uses_supplied_values_and_sampling_rate(monkeypatch):
    captured = {}

    def fake_build(card_id, data, rate, params):
        captured.update(card_id=card_id, data=data.copy(), rate=rate, params=params)
        return {"data": [], "layout": {}}

    monkeypatch.setattr(main, "build_analysis", fake_build)
    result = main.labeling_analysis(SignalAnalysisRequest(
        data=[0.25, -0.5, 0.75], sampling_rate_hz=25_600,
        sample_id="channel_1", card_id="dwt", params={"level": 2},
    ))

    assert result["sample_id"] == "channel_1"
    assert captured["card_id"] == "dwt"
    assert captured["rate"] == 25_600
    np.testing.assert_array_equal(captured["data"], [0.25, -0.5, 0.75])
    assert captured["params"] == {"level": 2}

    with pytest.raises(HTTPException) as error:
        main.labeling_analysis(SignalAnalysisRequest(
            data=[float("nan")], sampling_rate_hz=25_600, card_id="dwt",
        ))
    assert error.value.status_code == 422
