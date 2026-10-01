from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter


def write_wav(path: Path, *, channels: int = 1, sample_rate: int = 8_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = sample_rate // 20
    t = np.arange(frame_count, dtype=np.float64) / sample_rate
    tone = (np.sin(2 * np.pi * 440 * t) * 12_000).astype("<i2")
    if channels == 1:
        payload = tone.tobytes()
    else:
        payload = np.column_stack([tone] * channels).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(payload)
    return path


def write_tdms(
    path: Path, *, include_down: bool = True, sample_rate: int = 12_800
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.linspace(-1.0, 1.0, 256, dtype=np.float32)
    objects = [
        RootObject(),
        GroupObject("Vib Up_0"),
        ChannelObject(
            "Vib Up_0",
            "ACC",
            values,
            properties={"wf_increment": 1.0 / float(sample_rate)},
        ),
    ]
    if include_down:
        objects.extend(
            [
                GroupObject("Vib Down_0"),
                ChannelObject(
                    "Vib Down_0",
                    "ACC",
                    values[::-1],
                    properties={"wf_increment": 1.0 / float(sample_rate)},
                ),
            ]
        )
    with TdmsWriter(path) as writer:
        writer.write_segment(objects)
    return path
