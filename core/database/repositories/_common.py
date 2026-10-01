"""Shared repository helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath, PureWindowsPath
import sqlite3
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def as_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def json_text(value: Any, *, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize_relative_path(value: str) -> str:
    """Return a normalized POSIX relative path and reject root escape."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("relative_path must not be empty")
    if "\x00" in value:
        raise ValueError("relative_path contains a NUL byte")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        raise ValueError("relative_path must not be an absolute Windows path")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("relative_path must stay inside its storage root")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise ValueError("relative_path must name a file")
    lowered = normalized.casefold()
    if not lowered.endswith((".tdms.zst", ".wav")):
        raise ValueError("registered file must end with .tdms.zst or .wav")
    return normalized


def placeholders(count: int) -> str:
    if count < 1:
        raise ValueError("at least one value is required")
    return ", ".join("?" for _ in range(count))


def apply_updates(
    connection: sqlite3.Connection,
    *,
    table: str,
    key_sql: str,
    key_params: Sequence[Any],
    values: Mapping[str, Any],
    allowed_fields: frozenset[str],
    touch_updated_at: bool = True,
) -> int:
    unknown = set(values) - allowed_fields
    if unknown:
        raise ValueError(f"unsupported {table} fields: {', '.join(sorted(unknown))}")
    if not values:
        return 0
    assignments = [f'"{field}" = ?' for field in values]
    params = list(values.values())
    if touch_updated_at:
        assignments.append('"updated_at" = ?')
        params.append(utc_now())
    params.extend(key_params)
    cursor = connection.execute(
        f'UPDATE "{table}" SET {", ".join(assignments)} WHERE {key_sql}',
        params,
    )
    return int(cursor.rowcount)


__all__ = [
    "apply_updates",
    "as_dict",
    "as_dicts",
    "json_text",
    "normalize_relative_path",
    "placeholders",
    "utc_now",
]
