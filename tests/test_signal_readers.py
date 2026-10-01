from __future__ import annotations

import numpy as np
import soundfile as sf

from core.files.readers import read_signal
from tests.helpers import write_tdms


def test_read_signal_tdms_returns_complete_channel_and_rate(tmp_path):
    path = write_tdms(tmp_path / "sample.tdms", sample_rate=12_800)
    rate, values = read_signal(path, {"group_name": "Vib Up_0", "channel_name": "ACC"})
    assert rate == 12_800
    np.testing.assert_allclose(values, np.linspace(-1, 1, 256, dtype=np.float32))


def test_read_signal_wav_restores_amplitude_without_cropping(tmp_path):
    path = tmp_path / "motor-rfw.wav"
    sf.write(path, np.array([[0.1, -0.2], [0.3, -0.4]], dtype=np.float32), 51_200, subtype="FLOAT")
    rate, values = read_signal(path, {"channel_index": 1, "profile": "motor"})
    assert rate == 51_200
    np.testing.assert_allclose(values, [-2, -4], atol=1e-6)
