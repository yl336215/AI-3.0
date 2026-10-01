"""Batch condition editing and condition-combination filtering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import sqlite3
from typing import Any

from core.files import normalize_relative_path

from ..repositories import CONDITION_FIELDS, FileRepository
from ..schema import transaction


MAX_EXTRA_CONDITION_FIELDS = 32
MAX_EXTRA_CONDITION_KEY_LENGTH = 80
_EXTRA_FIELDS_KEY = "extra_fields"
_CONDITION_EXTRAS_METADATA_KEY = "condition_extras"
_MISSING = object()
_RESERVED_EXTRA_FIELD_NAMES = set(CONDITION_FIELDS) | {_EXTRA_FIELDS_KEY}


class ConditionService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.files = FileRepository(connection)

    @staticmethod
    def _validate_extra_fields(values: Any) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise TypeError("extra_fields must be an object")
        if len(values) > MAX_EXTRA_CONDITION_FIELDS:
            raise ValueError(
                f"extra_fields supports at most {MAX_EXTRA_CONDITION_FIELDS} fields"
            )
        normalized: dict[str, Any] = {}
        for raw_key, value in values.items():
            if not isinstance(raw_key, str):
                raise TypeError("extra field names must be strings")
            key = raw_key.strip()
            if not key:
                raise ValueError("extra field names must not be empty")
            if len(key) > MAX_EXTRA_CONDITION_KEY_LENGTH:
                raise ValueError(
                    "extra field names must not exceed "
                    f"{MAX_EXTRA_CONDITION_KEY_LENGTH} characters"
                )
            if key in _RESERVED_EXTRA_FIELD_NAMES:
                raise ValueError(f"extra field name is reserved: {key}")
            if key in normalized:
                raise ValueError(f"duplicate extra field name after trimming: {key}")
            if value is not None and not isinstance(value, (str, bool, int, float)):
                raise TypeError(f"extra field values must be scalar or null: {key}")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"extra field numeric values must be finite: {key}")
            normalized[key] = value
        return normalized

    @classmethod
    def _validate_conditions(
        cls, values: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        conditions = dict(values)
        raw_extras = conditions.pop(_EXTRA_FIELDS_KEY, _MISSING)
        unknown = set(conditions) - CONDITION_FIELDS
        if unknown:
            raise ValueError(f"unsupported condition fields: {', '.join(sorted(unknown))}")
        for field in ("load_value", "speed_ratio"):
            value = conditions.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise TypeError(f"{field} must be numeric or None")
        extras = (
            None
            if raw_extras is _MISSING
            else cls._validate_extra_fields(raw_extras)
        )
        return conditions, extras

    @staticmethod
    def _metadata_with_extra_patch(
        record: Mapping[str, Any], extra_patch: Mapping[str, Any]
    ) -> dict[str, Any]:
        raw_metadata = record.get("metadata_json")
        if raw_metadata in (None, ""):
            metadata: dict[str, Any] = {}
        elif isinstance(raw_metadata, str):
            loaded = json.loads(raw_metadata)
            if not isinstance(loaded, dict):
                raise ValueError("file metadata_json must contain an object")
            metadata = dict(loaded)
        elif isinstance(raw_metadata, Mapping):
            metadata = dict(raw_metadata)
        else:
            raise ValueError("file metadata_json must contain an object")

        raw_existing = metadata.get(_CONDITION_EXTRAS_METADATA_KEY, {})
        if raw_existing is None:
            raw_existing = {}
        if not isinstance(raw_existing, Mapping):
            raise ValueError("condition_extras metadata must contain an object")
        merged = dict(raw_existing)
        for key, value in extra_patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if len(merged) > MAX_EXTRA_CONDITION_FIELDS:
            raise ValueError(
                f"condition_extras supports at most {MAX_EXTRA_CONDITION_FIELDS} fields"
            )
        if merged:
            metadata[_CONDITION_EXTRAS_METADATA_KEY] = merged
        else:
            metadata.pop(_CONDITION_EXTRAS_METADATA_KEY, None)
        return metadata

    def _file_patch(
        self,
        file_uid: str,
        fixed_patch: Mapping[str, Any],
        extra_patch: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        patch = dict(fixed_patch)
        if extra_patch:
            record = self.files.get(file_uid)
            if record is None:
                raise KeyError(f"file does not exist: {file_uid}")
            patch["metadata_json"] = self._metadata_with_extra_patch(
                record, extra_patch
            )
        return patch

    def update_file(
        self,
        file_uid: str,
        conditions: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        fixed_patch, extra_patch = self._validate_conditions(conditions)
        return self.files.update(
            file_uid, self._file_patch(file_uid, fixed_patch, extra_patch)
        )

    def update_files(
        self,
        file_uids: Sequence[str],
        conditions: Mapping[str, Any],
    ) -> int:
        """Apply one condition patch to a batch atomically."""

        ids = list(dict.fromkeys(file_uids))
        if not ids:
            return 0
        fixed_patch, extra_patch = self._validate_conditions(conditions)
        if not fixed_patch and not extra_patch:
            return 0
        changed = 0
        with transaction(self.connection):
            for file_uid in ids:
                patch = self._file_patch(file_uid, fixed_patch, extra_patch)
                if self.files.update(file_uid, patch) is None:
                    raise KeyError(f"file does not exist: {file_uid}")
                changed += 1
        return changed

    def update_scope(
        self,
        conditions: Mapping[str, Any],
        *,
        file_uids: Sequence[str] | None = None,
        scope: str | None = None,
        relative_path: str | None = None,
    ) -> int:
        """Select active files by identity or path scope and patch them atomically."""
        if (file_uids is None) == (scope is None):
            raise ValueError("provide either file_uids or scope")
        if file_uids is not None:
            if relative_path is not None:
                raise ValueError("relative_path is only valid with scope")
            return self.update_files(file_uids, conditions)
        if scope not in {"database", "folder", "file"}:
            raise ValueError(f"unsupported update scope: {scope}")
        if scope == "database":
            if relative_path is not None:
                raise ValueError("relative_path must be omitted for database scope")
            selected = [record["file_uid"] for record in self.files.list(record_status="active", limit=None)]
        else:
            path = normalize_relative_path(str(relative_path or ""))
            records = self.files.list(record_status="active", limit=None)
            if scope == "file":
                selected = [record["file_uid"] for record in records if record["relative_path"] == path]
            else:
                selected = [record["file_uid"] for record in records if record["relative_path"].startswith(f"{path}/")]
        return self.update_files(selected, conditions)

    def filter_files(
        self,
        conditions: Mapping[str, Any] | None = None,
        *,
        filename: str | None = None,
        load_min: float | None = None,
        load_max: float | None = None,
        speed_min: float | None = None,
        speed_max: float | None = None,
        record_status: str | None = "active",
        availability_status: str | None = None,
        integrity_status: str | None = None,
        limit: int | None = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Filter with AND across fields and OR among values of one field."""

        query = dict(
            conditions=conditions,
            filename=filename,
            load_min=load_min,
            load_max=load_max,
            speed_min=speed_min,
            speed_max=speed_max,
            record_status=record_status,
            availability_status=availability_status,
            integrity_status=integrity_status,
        )
        counts = self.files.condition_counts(**query)
        rows = self.files.filter_conditions(**query, limit=limit, offset=offset)
        return {**counts, "files": rows}

    def distinct_values(
        self,
        field: str,
        *,
        record_status: str | None = "active",
        include_null: bool = False,
    ) -> list[Any]:
        if field not in CONDITION_FIELDS:
            raise ValueError(f"unsupported condition field: {field}")
        clauses: list[str] = []
        params: list[Any] = []
        if record_status is not None:
            clauses.append("record_status = ?")
            params.append(record_status)
        if not include_null:
            clauses.append(f'"{field}" IS NOT NULL')
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f'SELECT DISTINCT "{field}" FROM files{where} ORDER BY "{field}"',
            params,
        ).fetchall()
        return [row[0] for row in rows]


__all__ = ["ConditionService"]
