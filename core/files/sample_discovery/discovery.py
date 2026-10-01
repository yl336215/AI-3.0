"""Discover stable logical samples without inferring operating conditions."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml

from ..readers import open_tdms, channel_timing
from ..scanner import FileKind, detect_file_kind
from ..validators import validate_tdms, validate_wav
from .line_rules import channel_mappings_for_path


DiscoveryStatus = Literal[
    "ready",
    "pending_channel_mapping",
    "pending_channel_selection",
    "invalid",
]

DEFAULT_TDMS_CHANNELS: tuple[dict[str, str], ...] = (
    {
        "sample_id": "up",
        "display_name": "Up",
        "group_name": "Vib Up_0",
        "channel_name": "ACC",
    },
    {
        "sample_id": "down",
        "display_name": "Down",
        "group_name": "Vib Down_0",
        "channel_name": "ACC",
    },
)


@dataclass(frozen=True)
class DiscoveredSample:
    sample_id: str
    sample_scope: Literal["channel", "whole_file"]
    display_name: str
    locator_json: dict[str, object]
    sampling_rate_hz: float | None
    duration_s: float | None
    sort_order: int
    availability_status: str = "present"
    metadata_json: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SampleDiscoveryResult:
    kind: FileKind | None
    status: DiscoveryStatus
    samples: tuple[DiscoveredSample, ...] = ()
    issues: tuple[str, ...] = ()
    file_metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "samples": [sample.to_dict() for sample in self.samples],
            "issues": list(self.issues),
            "file_metadata": self.file_metadata,
        }


def default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "sample_profiles" / "default.yaml"


def load_sample_profile(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    profile_path = Path(path).expanduser() if path is not None else default_profile_path()
    if not profile_path.is_file():
        if path is None:
            return {"profile_id": "default", "channels": [dict(item) for item in DEFAULT_TDMS_CHANNELS]}
        raise FileNotFoundError(profile_path)
    text = profile_path.read_text(encoding="utf-8")
    payload: Any
    if profile_path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError(f"sample profile must contain an object: {profile_path}")
    return payload


def _channel_mappings(
    profile: Mapping[str, object] | Sequence[Mapping[str, object]] | None,
) -> list[dict[str, str]]:
    raw: object = profile
    if raw is None:
        raw = load_sample_profile()
    if isinstance(raw, Mapping):
        if "channels" in raw:
            raw = raw.get("channels")
        else:
            raw = [
                {"sample_id": str(sample_id), **dict(locator)}
                for sample_id, locator in raw.items()
                if isinstance(locator, Mapping)
            ]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("sample profile channels must be a list or mapping")

    mappings: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each channel mapping must be an object")
        sample_id = str(item.get("sample_id") or "").strip()
        group_name = str(item.get("group_name") or "").strip()
        channel_name = str(item.get("channel_name") or "").strip()
        if not sample_id or not group_name or not channel_name:
            raise ValueError("channel mapping requires sample_id, group_name and channel_name")
        if sample_id in seen:
            raise ValueError(f"duplicate sample_id in channel mapping: {sample_id}")
        seen.add(sample_id)
        mappings.append(
            {
                "sample_id": sample_id,
                "display_name": str(item.get("display_name") or sample_id),
                "group_name": group_name,
                "channel_name": channel_name,
            }
        )
    if not mappings:
        raise ValueError("sample profile contains no channel mappings")
    return mappings


def discover_tdms_samples(
    path: str | os.PathLike[str],
    *,
    profile: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
    manual_mapping: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
    line: str | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> SampleDiscoveryResult:
    source = Path(path)
    kind = detect_file_kind(source)
    if kind not in {"tdms", "tdms_zst"}:
        return SampleDiscoveryResult(kind, "invalid", issues=("not a TDMS import source",))
    validation = validate_tdms(source, temp_dir=temp_dir)
    if not validation.valid:
        return SampleDiscoveryResult(kind, "invalid", issues=validation.errors)
    try:
        selected_mapping = manual_mapping if manual_mapping is not None else profile
        if selected_mapping is None or (
            isinstance(selected_mapping, Mapping)
            and selected_mapping.get("resolver") == "line_rules"
        ):
            _, mappings = channel_mappings_for_path(
                source, line=line, metadata=validation.metadata
            )
        else:
            mappings = _channel_mappings(selected_mapping)
    except Exception as exc:
        return SampleDiscoveryResult(kind, "invalid", issues=(str(exc),), file_metadata=validation.metadata)

    samples: list[DiscoveredSample] = []
    issues: list[str] = []
    try:
        with open_tdms(
            source,
            compressed=(kind == "tdms_zst"),
            temp_dir=temp_dir,
        ) as tdms:
            groups = {group.name: group for group in tdms.groups()}
            for sort_order, mapping in enumerate(mappings):
                group_name = mapping["group_name"]
                channel_name = mapping["channel_name"]
                group = groups.get(group_name)
                if group is None:
                    issues.append(f"missing TDMS group: {group_name}")
                    continue
                channels = {channel.name: channel for channel in group.channels()}
                channel = channels.get(channel_name)
                if channel is None:
                    issues.append(f"missing TDMS channel: {group_name}/{channel_name}")
                    continue
                length = int(len(channel))
                sampling_rate_hz, duration_s = channel_timing(channel)
                samples.append(
                    DiscoveredSample(
                        sample_id=mapping["sample_id"],
                        sample_scope="channel",
                        display_name=mapping["display_name"],
                        locator_json={
                            "group_name": group_name,
                            "channel_name": channel_name,
                        },
                        sampling_rate_hz=sampling_rate_hz,
                        duration_s=duration_s,
                        sort_order=sort_order,
                        metadata_json={"length": length},
                    )
                )
    except Exception as exc:
        return SampleDiscoveryResult(
            kind,
            "invalid",
            issues=(f"{type(exc).__name__}: {exc}",),
            file_metadata=validation.metadata,
        )

    status: DiscoveryStatus = "ready" if samples and not issues else "pending_channel_mapping"
    return SampleDiscoveryResult(
        kind,
        status,
        samples=tuple(samples),
        issues=tuple(issues),
        file_metadata=validation.metadata,
    )


def discover_wav_samples(path: str | os.PathLike[str]) -> SampleDiscoveryResult:
    validation = validate_wav(path)
    if not validation.valid:
        return SampleDiscoveryResult("wav", "invalid", issues=validation.errors)
    metadata = validation.metadata
    channels = int(metadata.get("channels") or 0)
    if channels != 1:
        return SampleDiscoveryResult(
            "wav",
            "pending_channel_selection",
            issues=(f"multi-channel WAV requires an explicit channel selection: {channels} channels",),
            file_metadata=metadata,
        )
    sample = DiscoveredSample(
        sample_id="main",
        sample_scope="whole_file",
        display_name="Main",
        locator_json={"channel_index": 0},
        sampling_rate_hz=float(metadata.get("sampling_rate_hz") or 0) or None,
        duration_s=float(metadata.get("duration_s") or 0) or None,
        sort_order=0,
        metadata_json={
            "frames": int(metadata.get("frames") or 0),
            "channels": channels,
        },
    )
    return SampleDiscoveryResult("wav", "ready", samples=(sample,), file_metadata=metadata)


def discover_samples(
    path: str | os.PathLike[str],
    *,
    profile: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
    manual_mapping: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
    line: str | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> SampleDiscoveryResult:
    kind = detect_file_kind(path)
    if kind in {"tdms", "tdms_zst"}:
        return discover_tdms_samples(
            path,
            profile=profile,
            manual_mapping=manual_mapping,
            line=line,
            temp_dir=temp_dir,
        )
    if kind == "wav":
        return discover_wav_samples(path)
    return SampleDiscoveryResult(kind, "invalid", issues=("unsupported file suffix",))
