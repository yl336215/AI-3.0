"""Repository for stable per-file sample records."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any

from ._common import apply_updates, as_dict, as_dicts, json_text, placeholders, utc_now


_UPDATABLE_FIELDS = frozenset(
    {
        "sample_scope",
        "display_name",
        "sampling_rate_hz",
        "duration_s",
        "sort_order",
        "availability_status",
        "metadata_json",
    }
)


class SampleBindingConflict(RuntimeError):
    """Raised when discovery tries to rebind an existing sample id."""


class SampleRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        required = {"file_uid", "sample_id", "sample_scope", "display_name", "locator_json"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"missing sample fields: {', '.join(sorted(missing))}")
        allowed = required | _UPDATABLE_FIELDS | {"created_at", "updated_at"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported sample fields: {', '.join(sorted(unknown))}")

        now = utc_now()
        record = {
            "file_uid": values["file_uid"],
            "sample_id": values["sample_id"],
            "sample_scope": values["sample_scope"],
            "display_name": values["display_name"],
            "locator_json": json_text(values["locator_json"], default={}),
            "sampling_rate_hz": values.get("sampling_rate_hz"),
            "duration_s": values.get("duration_s"),
            "sort_order": values.get("sort_order", 0),
            "availability_status": values.get("availability_status", "present"),
            "metadata_json": json_text(values.get("metadata_json"), default={}),
            "created_at": values.get("created_at", now),
            "updated_at": values.get("updated_at", now),
        }
        columns = tuple(record)
        self.connection.execute(
            f"INSERT INTO samples ({', '.join(columns)}) VALUES ({placeholders(len(columns))})",
            tuple(record[column] for column in columns),
        )
        import json
        locator = values.get("locator_json") or {}
        if isinstance(locator, str):
            locator = json.loads(locator)
        start_s = float(locator.get("start_s", 0.0))
        end_value = locator.get("end_s", values.get("duration_s"))
        end_s = float(end_value) if end_value is not None else start_s
        self.connection.execute(
            """
            INSERT INTO sample_records (file_uid, sample_id, sample_scope)
            VALUES (?, ?, ?)
            """,
            (
                record["file_uid"],
                record["sample_id"],
                json_text({"start_s": start_s, "end_s": end_s}, default={}),
            ),
        )
        created = self.get(str(record["file_uid"]), str(record["sample_id"]))
        assert created is not None
        return created

    def get(self, file_uid: str, sample_id: str) -> dict[str, Any] | None:
        return as_dict(
            self.connection.execute(
                "SELECT * FROM samples WHERE file_uid = ? AND sample_id = ?",
                (file_uid, sample_id),
            ).fetchone()
        )

    def list_by_file(self, file_uid: str) -> list[dict[str, Any]]:
        return as_dicts(
            self.connection.execute(
                """
                SELECT * FROM samples
                WHERE file_uid = ?
                ORDER BY sort_order, sample_id
                """,
                (file_uid,),
            ).fetchall()
        )

    def update(
        self,
        file_uid: str,
        sample_id: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        prepared = dict(values)
        if "metadata_json" in prepared:
            prepared["metadata_json"] = json_text(prepared["metadata_json"], default={})
        changed = apply_updates(
            self.connection,
            table="samples",
            key_sql="file_uid = ? AND sample_id = ?",
            key_params=(file_uid, sample_id),
            values=prepared,
            allowed_fields=_UPDATABLE_FIELDS,
        )
        return self.get(file_uid, sample_id) if changed else None

    def delete(self, file_uid: str, sample_id: str) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM samples WHERE file_uid = ? AND sample_id = ?",
                (file_uid, sample_id),
            ).rowcount
        )

    def register_discovered(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Create a sample or return its identical binding without rebinding it."""

        file_uid = str(values["file_uid"])
        sample_id = str(values["sample_id"])
        existing = self.get(file_uid, sample_id)
        if existing is None:
            return self.create(values)
        requested_locator = json_text(values["locator_json"], default={})
        existing_locator = json_text(existing["locator_json"], default={})
        if requested_locator != existing_locator:
            raise SampleBindingConflict(
                f"sample {file_uid}/{sample_id} is already bound to another locator"
            )
        return existing


__all__ = ["SampleBindingConflict", "SampleRepository"]
