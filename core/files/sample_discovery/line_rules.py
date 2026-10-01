"""Resolve TDMS logical samples from AI-3.0's independent line rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


def default_line_rules_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "tdms_rules.yaml"


def load_line_rules(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    rules_path = Path(path).expanduser() if path is not None else default_line_rules_path()
    with rules_path.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"line_rules configuration is invalid: {rules_path}")
    return payload


def _logical_stem(path: Path) -> str:
    name = path.name
    lowered = name.lower()
    for suffix in (".tdms.zst", ".tdms"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _filename_metadata(path: Path, rule: Mapping[str, Any]) -> dict[str, str]:
    parts = _logical_stem(path).split(str(rule.get("split") or "_"))

    def pick(spec: object) -> str:
        if isinstance(spec, int):
            return parts[spec] if 0 <= spec < len(parts) else "UNKNOWN"
        if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
            values = [parts[index] for index in spec if isinstance(index, int) and 0 <= index < len(parts)]
            return "_".join(values) if values else "UNKNOWN"
        return "UNKNOWN"

    return {
        "sn": pick(rule.get("sn_index")),
        "reference": pick(rule.get("reference_index")),
        "time": pick(rule.get("time_index")),
    }


def _resolve_line(path: Path, rules: Mapping[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any]]:
    valid = [name for name, rule in rules.items() if isinstance(rule, Mapping) and rule.get("filename") and rule.get("channels")]
    path_tokens = {part.casefold() for part in path.parts}
    matched = [name for name in valid if name.casefold() in path_tokens]
    if len(matched) == 1:
        name = matched[0]
        return name, rules[name]

    stem = _logical_stem(path)
    if stem.startswith("E-Tilt_") and "etilt1" in rules:
        return "etilt1", rules["etilt1"]
    if "_L4-C162-" in stem and "epump4" in rules:
        return "epump4", rules["epump4"]
    raise ValueError("cannot infer line from TDMS path using config/tdms_rules.yaml")


def _resolve_channels(channels: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    conditional = channels.get("conditional")
    if not conditional:
        return channels
    for item in conditional:
        if isinstance(item, Mapping) and (item.get("when") or {}).get("reference") == reference:
            return item
    for item in conditional:
        if isinstance(item, Mapping) and (item.get("when") or {}).get("reference") == "*":
            return item
    raise ValueError(f"no channel rule matches reference: {reference}")


def _as_mappings(channels: Mapping[str, Any]) -> list[dict[str, str]]:
    acc_channel = str(channels["acc_channel"])
    return [
        {"sample_id": "up", "display_name": "Up", "group_name": str(channels["up_group"]), "channel_name": acc_channel},
        {"sample_id": "down", "display_name": "Down", "group_name": str(channels["down_group"]), "channel_name": acc_channel},
    ]


def _mapping_exists(mapping: Sequence[Mapping[str, str]], metadata: Mapping[str, Any]) -> bool:
    available = {
        (str(group.get("name")), str(channel.get("name")))
        for group in metadata.get("groups", [])
        if isinstance(group, Mapping)
        for channel in group.get("channels", [])
        if isinstance(channel, Mapping)
    }
    return all((item["group_name"], item["channel_name"]) in available for item in mapping)


def channel_mappings_for_path(
    path: str | Path,
    *,
    line: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    source = Path(path)
    rules = load_line_rules()
    if line is not None:
        normalized_line = line.strip()
        if normalized_line not in rules:
            raise ValueError(f"line is not defined in config/tdms_rules.yaml: {normalized_line}")
        line, rule = normalized_line, rules[normalized_line]
    else:
        try:
            line, rule = _resolve_line(source, rules)
        except ValueError:
            if metadata is None:
                raise
            candidates: dict[
                tuple[tuple[str, str], ...], tuple[str, list[dict[str, str]]]
            ] = {}
            for candidate_line, candidate_rule in rules.items():
                channels_rule = candidate_rule.get("channels") or {}
                alternatives = channels_rule.get("conditional") or [channels_rule]
                for alternative in alternatives:
                    if not isinstance(alternative, Mapping):
                        continue
                    mapping = _as_mappings(alternative)
                    signature = tuple(
                        (item["group_name"], item["channel_name"])
                        for item in mapping
                    )
                    if _mapping_exists(mapping, metadata):
                        candidates.setdefault(signature, (candidate_line, mapping))
            if len(candidates) == 1:
                return next(iter(candidates.values()))
            raise ValueError(
                "cannot infer a unique line/channel mapping from TDMS path or contents"
            )
    filename = rule.get("filename") or {}
    metadata = _filename_metadata(source, filename)
    channels = _resolve_channels(rule["channels"], metadata["reference"])
    return line, _as_mappings(channels)
