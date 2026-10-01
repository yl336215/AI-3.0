"""WAV metadata, channel layout and complete signal reader."""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml


READ_FRAMES = 65536
def load_wav_rules(path: str | Path | None = None) -> dict[str, dict[str, object]]:
    rules_path = Path(path) if path is not None else Path(__file__).resolve().parents[3] / "config" / "wav_rules.yaml"
    with rules_path.open("r", encoding="utf-8") as stream:
        rules = yaml.safe_load(stream)
    if not isinstance(rules, dict) or not rules:
        raise ValueError(f"invalid WAV rules: {rules_path}")
    for name, rule in rules.items():
        if not isinstance(rule, dict) or not isinstance(rule.get("channels"), list) or not rule["channels"]:
            raise ValueError(f"invalid WAV channel rules: {name}")
        indices = [item.get("index") for item in rule["channels"] if isinstance(item, dict)]
        ids = [item.get("sample_id") for item in rule["channels"] if isinstance(item, dict)]
        if (len(indices) != len(rule["channels"]) or indices != list(range(len(indices)))
                or len(ids) != len(set(ids)) or not all(ids)
                or not all(item.get("display_name") for item in rule["channels"])
                or not isinstance(rule.get("amplitude_scale"), (int, float))
                or rule["amplitude_scale"] <= 0
                or not isinstance(rule.get("direction_suffixes"), dict)):
            raise ValueError(f"invalid WAV channel rules: {name}")
    return rules


def describe_wav(path: str | Path, profile: str | None = None) -> dict[str, object]:
    """Read actual WAV metadata and apply only the explicitly selected type."""
    source = Path(path)
    info = sf.info(source)
    rules = load_wav_rules()
    if profile is not None and profile not in rules:
        raise ValueError(f"Unsupported WAV profile: {profile}")
    rule = rules.get(profile) if profile else None
    if rule is not None:
        mappings = rule["channels"]
        if not 1 <= info.channels <= len(mappings):
            raise ValueError(f"{rule['display_name']} WAV 需要 1 至 {len(mappings)} 通道")
    else:
        profile = "generic"
        mappings = [
            {"index": index, "sample_id": "audio" if info.channels == 1 else f"channel_{index + 1}",
             "display_name": "Audio" if info.channels == 1 else f"Channel {index + 1}"}
            for index in range(info.channels)
        ]
    suffix = source.stem.lower().rsplit("-", 1)[-1]
    direction = rule["direction_suffixes"].get(suffix, "") if rule else ""
    return {
        "profile": profile,
        "direction": direction,
        "sampling_rate_hz": int(info.samplerate),
        "frames": int(info.frames),
        "channels": int(info.channels),
        "channel_names": tuple(item["display_name"] for item in mappings),
        "channel_mappings": mappings,
    }


def read_wav_channel(path: str | Path, channel_index: int, profile: str | None = None) -> tuple[int, np.ndarray]:
    """Return the complete channel, applying the selected type's amplitude scale."""
    rules = load_wav_rules() if profile else {}
    if profile is not None and profile not in rules:
        raise ValueError(f"Unsupported WAV profile: {profile}")
    scale = float(rules[profile]["amplitude_scale"]) if profile else 1.0
    with sf.SoundFile(path) as stream:
        if not 0 <= channel_index < stream.channels:
            raise IndexError(f"WAV channel index out of range: {channel_index}")
        values = stream.read(dtype="float32", always_2d=True)
        return int(stream.samplerate), np.asarray(values[:, channel_index] * scale, dtype=np.float32)


def _soundfile_metadata(path: Path, *, verify_all_frames: bool) -> dict[str, object]:
    with sf.SoundFile(path, mode="r") as stream:
        channels = int(stream.channels)
        frames = int(stream.frames)
        sampling_rate_hz = int(stream.samplerate)
        if verify_all_frames:
            read_frames = 0
            while True:
                block = stream.read(READ_FRAMES, always_2d=True)
                if len(block) == 0:
                    break
                read_frames += int(len(block))
            if read_frames != frames:
                raise ValueError(
                    f"WAV frame count mismatch: header={frames}, decoded={read_frames}"
                )
        return {
            "channels": channels,
            "frames": frames,
            "sampling_rate_hz": sampling_rate_hz,
            "duration_s": frames / sampling_rate_hz if sampling_rate_hz else 0.0,
            "format": str(stream.format),
            "subtype": str(stream.subtype),
        }


def _stdlib_metadata(path: Path, *, verify_all_frames: bool) -> dict[str, object]:
    with wave.open(str(path), "rb") as stream:
        channels = int(stream.getnchannels())
        frames = int(stream.getnframes())
        sampling_rate_hz = int(stream.getframerate())
        sample_width = int(stream.getsampwidth())
        if verify_all_frames:
            payload = stream.readframes(frames)
            expected = frames * channels * sample_width
            if len(payload) != expected:
                raise ValueError(
                    f"WAV payload is truncated: expected={expected}, actual={len(payload)}"
                )
        return {
            "channels": channels,
            "frames": frames,
            "sampling_rate_hz": sampling_rate_hz,
            "duration_s": frames / sampling_rate_hz if sampling_rate_hz else 0.0,
            "format": "WAV",
            "subtype": f"PCM_{sample_width * 8}",
        }


def read_wav_metadata(
    path: str | os.PathLike[str], *, verify_all_frames: bool = True
) -> dict[str, object]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        return _soundfile_metadata(source, verify_all_frames=verify_all_frames)
    except ImportError:
        return _stdlib_metadata(source, verify_all_frames=verify_all_frames)
