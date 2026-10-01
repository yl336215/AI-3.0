from __future__ import annotations

import io
import wave
import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from core.files.readers import describe_wav, read_wav_channel
from core.algorithms.preprocess.highpass import apply as highpass_filter
from web.api import main
from web.api.models import TdmsLabelResolveRequest


def _audio_samples(response):
    with wave.open(io.BytesIO(response.body)) as audio:
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float64) / 32767


def test_wav_audio_normalizes_full_scaled_channel_before_cropping(tmp_path):
    path = tmp_path / "motor-rfw.wav"
    frames = np.zeros((200, 4), dtype=np.float32)
    frames[:100, 0] = 0.02
    frames[100:, 0] = 0.04
    sf.write(path, frames, 100, subtype="FLOAT")

    whole = main.labeling_audio(str(path), "channel_1", wav_profile="motor", low_frequency_filter=False)
    event = main.labeling_audio(str(path), "channel_1", 0.0, 1.0, wav_profile="motor", low_frequency_filter=False)
    whole_values = _audio_samples(whole)
    event_values = _audio_samples(event)
    assert len(whole_values) == 200
    assert len(event_values) == 100
    assert np.sqrt(np.mean(whole_values ** 2)) == pytest.approx(0.3, abs=1 / 32767)
    np.testing.assert_allclose(event_values, whole_values[:100], atol=1 / 32767)


def test_motor_wav_channels_direction_and_external_scaled_read(tmp_path):
    path = tmp_path / "123-rfw.wav"
    samples = np.tile(np.array([[0.1, -0.2, 0.3, -0.4]], dtype=np.float32), (100, 1))
    sf.write(path, samples, 51_200, subtype="FLOAT")

    assert describe_wav(path, "motor")["direction"] == "正转"
    discovered = main._label_file_samples(path, wav_profile="motor")
    assert [item["display_name"] for item in discovered] == [
        "电机上方 Z 向加速度", "电机上方 Y 向加速度", "电流", "霍尔",
    ]
    assert [item["sample_id"] for item in discovered] == ["channel_1", "channel_2", "channel_3", "channel_4"]
    assert [item["locator"]["channel_index"] for item in discovered] == [0, 1, 2, 3]
    assert all(item["sampling_rate_hz"] == 51_200 for item in discovered)
    rate, values = read_wav_channel(path, 1, "motor")
    assert rate == 51_200
    np.testing.assert_allclose(values, -2.0, rtol=1e-6)
    np.testing.assert_allclose(main._read_label_signal(path, discovered[2]), 3.0, rtol=1e-6)


def test_rail_wav_channels_and_reverse_direction(tmp_path):
    path = tmp_path / "722645-rbf.wav"
    sf.write(path, np.zeros((100, 4), dtype=np.float32), 25_600, subtype="FLOAT")

    assert describe_wav(path, "rail")["direction"] == "反转"
    discovered = main._label_file_samples(path, wav_profile="rail")
    assert [item["display_name"] for item in discovered] == [
        "左轨 Y 向加速度", "右轨 Y 向加速度", "左侧侧板 Y 向加速度", "右侧侧板 Y 向加速度",
    ]
    assert all(item["locator"]["profile"] == "rail" for item in discovered)
    assert all(item["sampling_rate_hz"] == 25_600 for item in discovered)


def test_wav_type_and_direction_are_selected_from_rules(tmp_path):
    path = tmp_path / "722645-rbw.wav"
    sf.write(path, np.zeros((100, 4), dtype=np.float32), 25_600, subtype="FLOAT")

    assert describe_wav(path)["profile"] == "generic"
    assert describe_wav(path, "motor")["direction"] == "反转"
    assert describe_wav(path, "rail")["direction"] == ""
    assert main._label_file_samples(path, wav_profile="motor")[0]["display_name"] == "电机上方 Z 向加速度"


def test_missing_motor_channels_keep_empty_named_positions(tmp_path):
    path = tmp_path / "162913-rfw.wav"
    sf.write(path, np.zeros((100, 2), dtype=np.float32), 51_200, subtype="FLOAT")
    discovered = main._label_file_samples(path, wav_profile="motor")
    assert [item["display_name"] for item in discovered] == [
        "电机上方 Z 向加速度", "电机上方 Y 向加速度", "电流", "霍尔",
    ]
    assert [item["missing"] for item in discovered] == [False, False, True, True]
    assert describe_wav(path, "motor")["direction"] == "正转"
    with pytest.raises(HTTPException) as error:
        main.labeling_waveform(str(path), "channel_4", wav_profile="motor")
    assert error.value.status_code == 404


def test_missing_rail_channel_keeps_empty_position(tmp_path):
    path = tmp_path / "162913-rbf.wav"
    sf.write(path, np.zeros((100, 3), dtype=np.float32), 25_600, subtype="FLOAT")
    discovered = main._label_file_samples(path, wav_profile="rail")
    assert [item["missing"] for item in discovered] == [False, False, False, True]
    assert describe_wav(path, "rail")["direction"] == "反转"


def test_existing_generic_wav_remains_readable(tmp_path):
    path = tmp_path / "other.wav"
    sf.write(path, np.array([0.25, -0.25], dtype=np.float32), 8_000, subtype="FLOAT")
    assert main._label_file_samples(path)[0]["display_name"] == "Audio"
    rate, values = read_wav_channel(path, 0)
    assert rate == 8_000
    np.testing.assert_allclose(values, [0.25, -0.25])


def test_wav_waveform_returns_scaled_raw_highpass_and_mel(tmp_path):
    path = tmp_path / "motor-rbw.wav"
    frames = np.zeros((2_560, 4), dtype=np.float32)
    frames[:, 0] = 0.125
    sf.write(path, frames, 51_200, subtype="FLOAT")

    payload = main.labeling_waveform(str(path), "channel_1", wav_profile="motor", low_frequency_filter=True)
    assert payload["sampling_rate_hz"] == 51_200
    assert payload["channel_name"] == "电机上方 Z 向加速度"
    assert payload["values"][0] == 1.25
    assert len(payload["highpass_20hz_values"]) == len(payload["values"])
    assert np.max(np.abs(payload["highpass_20hz_values"][100:])) < 0.05
    assert payload["mel_db"]
    assert len(payload["mel_freqs"]) == 39
    assert "pcen" not in payload
    assert "mfcc" not in payload


def test_highpass_removes_sub_20hz_component_and_preserves_higher_tone():
    rate = 1_000
    time = np.arange(rate * 4) / rate
    low = np.sin(2 * np.pi * 5 * time)
    high = np.sin(2 * np.pi * 100 * time)
    filtered = highpass_filter(low + high, rate)
    central = slice(rate, 3 * rate)
    assert np.sqrt(np.mean((filtered[central] - high[central]) ** 2)) < 0.02


def test_wav_filter_switch_controls_mel_payload_and_playback(tmp_path, monkeypatch):
    path = tmp_path / "motor-rfw.wav"
    rate = 8_000
    time = np.arange(rate * 2) / rate
    frames = np.zeros((time.size, 4), dtype=np.float32)
    frames[:, 0] = 0.2 + 0.05 * np.sin(2 * np.pi * 100 * time)
    sf.write(path, frames, rate, subtype="FLOAT")

    filtered = main.labeling_waveform(str(path), "channel_1", wav_profile="motor", low_frequency_filter=True)
    filtered_audio = _audio_samples(main.labeling_audio(str(path), "channel_1", wav_profile="motor", low_frequency_filter=True))
    assert filtered["low_frequency_filter"] is True
    assert len(filtered["highpass_20hz_values"]) == time.size
    assert abs(np.mean(filtered_audio[rate // 2:-rate // 2])) < 0.01

    def unexpected_filter(*_args, **_kwargs):
        raise AssertionError("滤波关闭时不应计算高通")

    monkeypatch.setattr(main, "highpass_filter", unexpected_filter)
    unfiltered = main.labeling_waveform(str(path), "channel_1", wav_profile="motor", low_frequency_filter=False)
    raw_audio = _audio_samples(main.labeling_audio(str(path), "channel_1", wav_profile="motor", low_frequency_filter=False))
    default_waveform = main.labeling_waveform(str(path), "channel_1", wav_profile="motor")
    default_audio = _audio_samples(main.labeling_audio(str(path), "channel_1", wav_profile="motor"))
    assert unfiltered["low_frequency_filter"] is False
    assert unfiltered["highpass_20hz_values"] is None
    assert len(unfiltered["mel_freqs"]) == 39
    assert np.mean(raw_audio) > 0.1
    assert default_waveform["highpass_20hz_values"] is None
    np.testing.assert_array_equal(default_audio, raw_audio)


def test_resolve_wav_exposes_profile_direction_and_named_channels(tmp_path):
    path = tmp_path / "722645-rbf.wav"
    sf.write(path, np.zeros((100, 4), dtype=np.float32), 25_600, subtype="FLOAT")

    resolved = main.resolve_labeling_file(TdmsLabelResolveRequest(path=str(path), line="", wav_profile="rail"))
    assert resolved["metadata"] == {"wav_profile": "rail", "direction": "反转"}
    assert [item["display_name"] for item in resolved["samples"]] == [
        "左轨 Y 向加速度", "右轨 Y 向加速度", "左侧侧板 Y 向加速度", "右侧侧板 Y 向加速度",
    ]


def test_selected_wav_profile_rejects_extra_channels(tmp_path):
    path = tmp_path / "wrong-rbw.wav"
    sf.write(path, np.zeros((100, 5), dtype=np.float32), 25_600, subtype="FLOAT")
    with pytest.raises(ValueError, match="电机 WAV"):
        describe_wav(path, "motor")


def test_long_wav_waveform_and_audio_keep_both_edges(tmp_path):
    path = tmp_path / "motor-rfw.wav"
    frames = np.zeros((102_400, 4), dtype=np.float32)
    frames[0, 0] = 0.25
    frames[-1, 0] = -0.25
    sf.write(path, frames, 51_200, subtype="FLOAT")

    payload = main.labeling_waveform(str(path), "channel_1", wav_profile="motor")
    assert payload["cut_start_s"] == 0
    assert payload["cut_end_s"] == 2
    assert payload["cut_values"] is None
    assert payload["values"][0] == 2.5
    assert payload["values"][-1] == -2.5
    response = main.labeling_audio(str(path), "channel_1", wav_profile="motor")
    assert float(response.headers["X-Audio-Start-S"]) == 0
    assert float(response.headers["X-Audio-End-S"]) == 2
