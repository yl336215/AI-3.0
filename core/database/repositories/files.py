"""Repository for file records and condition-aware queries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import sqlite3
from typing import Any
from uuid import uuid4

from ._common import (
    apply_updates,
    as_dict,
    as_dicts,
    json_text,
    normalize_relative_path,
    placeholders,
    utc_now,
)


CONDITION_FIELDS = frozenset(
    {
        "line",
        "device_id",
        "model_name",
        "reference",
        "load_value",
        "load_unit",
        "speed_ratio",
        "acquired_at",
    }
)

_CREATE_REQUIRED = frozenset(
    {
        "storage_id",
        "relative_path",
        "payload_sha256",
        "stored_sha256",
        "payload_size_bytes",
        "stored_size_bytes",
        "stored_mtime_ns",
    }
)

_UPDATABLE_FIELDS = frozenset(
    {
        "storage_id",
        "relative_path",
        *CONDITION_FIELDS,
        "payload_sha256",
        "stored_sha256",
        "payload_size_bytes",
        "stored_size_bytes",
        "stored_mtime_ns",
        "record_status",
        "availability_status",
        "integrity_status",
        "last_seen_at",
        "last_verified_at",
        "metadata_json",
    }
)


class FileRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        missing = _CREATE_REQUIRED - set(values)
        if missing:
            raise ValueError(f"missing file fields: {', '.join(sorted(missing))}")
        unknown = set(values) - (_UPDATABLE_FIELDS | {"file_uid", "created_at", "updated_at"})
        if unknown:
            raise ValueError(f"unsupported file fields: {', '.join(sorted(unknown))}")

        now = utc_now()
        record: dict[str, Any] = {
            "file_uid": str(values.get("file_uid") or uuid4()),
            "storage_id": values["storage_id"],
            "relative_path": normalize_relative_path(str(values["relative_path"])),
            "line": values.get("line"),
            "device_id": values.get("device_id"),
            "model_name": values.get("model_name"),
            "reference": values.get("reference"),
            "load_value": values.get("load_value"),
            "load_unit": values.get("load_unit"),
            "speed_ratio": values.get("speed_ratio"),
            "acquired_at": values.get("acquired_at"),
            "payload_sha256": values["payload_sha256"],
            "stored_sha256": values["stored_sha256"],
            "payload_size_bytes": values["payload_size_bytes"],
            "stored_size_bytes": values["stored_size_bytes"],
            "stored_mtime_ns": values["stored_mtime_ns"],
            "record_status": values.get("record_status", "active"),
            "availability_status": values.get("availability_status", "present"),
            "integrity_status": values.get("integrity_status", "verified"),
            "last_seen_at": values.get("last_seen_at"),
            "last_verified_at": values.get("last_verified_at"),
            "metadata_json": json_text(values.get("metadata_json"), default={}),
            "created_at": values.get("created_at", now),
            "updated_at": values.get("updated_at", now),
        }
        columns = tuple(record)
        self.connection.execute(
            f"INSERT INTO files ({', '.join(columns)}) VALUES ({placeholders(len(columns))})",
            tuple(record[column] for column in columns),
        )
        conditions = {
            "line": record["line"],
            "device_id": record["device_id"],
            "model_name": record["model_name"],
            "reference": record["reference"],
            "load_value": record["load_value"],
            "load_unit": record["load_unit"],
            "speed_ratio": record["speed_ratio"],
            "timestamp": record["acquired_at"],
        }
        raw_metadata = values.get("metadata_json") or {}
        if isinstance(raw_metadata, str):
            import json
            raw_metadata = json.loads(raw_metadata)
        metadata = dict(raw_metadata.get("condition_extras") or {})
        self.connection.execute(
            """
            INSERT INTO file_records (
                file_uid, storage_id, relative_path, conditions, metadata
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                record["file_uid"], record["storage_id"], record["relative_path"],
                json_text(conditions, default={}), json_text(metadata, default={}),
            ),
        )
        created = self.get(record["file_uid"])
        assert created is not None
        return created

    def get(self, file_uid: str) -> dict[str, Any] | None:
        return as_dict(
            self.connection.execute(
                "SELECT * FROM files WHERE file_uid = ?", (file_uid,)
            ).fetchone()
        )

    def get_by_path(
        self,
        storage_id: str,
        relative_path: str,
        *,
        active_only: bool = True,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM files WHERE storage_id = ? "
            "AND relative_path = ? COLLATE AI3_PATH"
        )
        params: list[Any] = [storage_id, normalize_relative_path(relative_path)]
        if active_only:
            sql += " AND record_status = 'active'"
        sql += " ORDER BY created_at DESC LIMIT 1"
        return as_dict(self.connection.execute(sql, params).fetchone())

    def list(
        self,
        *,
        record_status: str | None = "active",
        limit: int | None = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM files"
        params: list[Any] = []
        if record_status is not None:
            sql += " WHERE record_status = ?"
            params.append(record_status)
        sql += " ORDER BY created_at DESC, file_uid"
        if limit is not None:
            if limit < 0 or offset < 0:
                raise ValueError("limit and offset must be non-negative")
            sql += " LIMIT ? OFFSET ?"
            params.extend((limit, offset))
        return as_dicts(self.connection.execute(sql, params).fetchall())

    def update(self, file_uid: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        prepared = dict(values)
        if "relative_path" in prepared:
            prepared["relative_path"] = normalize_relative_path(str(prepared["relative_path"]))
        if "metadata_json" in prepared:
            prepared["metadata_json"] = json_text(prepared["metadata_json"], default={})
        changed = apply_updates(
            self.connection,
            table="files",
            key_sql="file_uid = ?",
            key_params=(file_uid,),
            values=prepared,
            allowed_fields=_UPDATABLE_FIELDS,
        )
        updated = self.get(file_uid) if changed else None
        if updated is not None:
            conditions = {
                "line": updated["line"],
                "device_id": updated["device_id"],
                "model_name": updated["model_name"],
                "reference": updated["reference"],
                "load_value": updated["load_value"],
                "load_unit": updated["load_unit"],
                "speed_ratio": updated["speed_ratio"],
                "timestamp": updated["acquired_at"],
            }
            import json
            raw_metadata = updated.get("metadata_json") or "{}"
            metadata_payload = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
            metadata = dict(metadata_payload.get("condition_extras") or {})
            self.connection.execute(
                """
                UPDATE file_records
                SET storage_id = ?, relative_path = ?, conditions = ?, metadata = ?
                WHERE file_uid = ?
                """,
                (
                    updated["storage_id"], updated["relative_path"],
                    json_text(conditions, default={}), json_text(metadata, default={}),
                    file_uid,
                ),
            )
        return updated

    def delete(self, file_uid: str) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM files WHERE file_uid = ?", (file_uid,)
            ).rowcount
        )

    def find_by_payload_sha256(
        self,
        payload_sha256: str,
        *,
        record_status: str | None = "active",
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM files WHERE payload_sha256 = ?"
        params: list[Any] = [payload_sha256]
        if record_status is not None:
            sql += " AND record_status = ?"
            params.append(record_status)
        sql += " ORDER BY storage_id, relative_path"
        return as_dicts(self.connection.execute(sql, params).fetchall())

    def mark_missing(self, file_uid: str, *, seen_at: str | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {"availability_status": "missing"}
        if seen_at is not None:
            values["last_seen_at"] = seen_at
        return self.update(file_uid, values)

    def mark_present(
        self,
        file_uid: str,
        *,
        seen_at: str | None = None,
        integrity_status: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {"availability_status": "present"}
        if seen_at is not None:
            values["last_seen_at"] = seen_at
        if integrity_status is not None:
            values["integrity_status"] = integrity_status
        return self.update(file_uid, values)

    def supersede(self, file_uid: str) -> dict[str, Any] | None:
        return self.update(file_uid, {"record_status": "superseded"})

    @staticmethod
    def _filter_sql(
        *,
        conditions: Mapping[str, Any] | None,
        filename: str | None,
        load_min: float | None,
        load_max: float | None,
        speed_min: float | None,
        speed_max: float | None,
        record_status: str | None,
        availability_status: str | None,
        integrity_status: str | None,
    ) -> tuple[str, list[Any]]:
        filters = dict(conditions or {})
        unknown = set(filters) - CONDITION_FIELDS
        if unknown:
            raise ValueError(f"unsupported condition fields: {', '.join(sorted(unknown))}")
        if load_min is not None and load_max is not None and load_min > load_max:
            raise ValueError("load_min must not exceed load_max")
        if speed_min is not None and speed_max is not None and speed_min > speed_max:
            raise ValueError("speed_min must not exceed speed_max")

        clauses: list[str] = []
        params: list[Any] = []
        if record_status is not None:
            clauses.append("f.record_status = ?")
            params.append(record_status)
        if availability_status is not None:
            clauses.append("f.availability_status = ?")
            params.append(availability_status)
        if integrity_status is not None:
            clauses.append("f.integrity_status = ?")
            params.append(integrity_status)
        if filename:
            # ``instr`` treats SQL wildcard characters literally, so a file
            # name such as ``run_100%.wav`` can be searched without LIKE
            # escaping.  Search the complete relative path because the data
            # panel also displays and identifies files by that path.
            clauses.append("instr(lower(f.relative_path), lower(?)) > 0")
            params.append(filename)

        for field, raw_values in filters.items():
            if isinstance(raw_values, Sequence) and not isinstance(
                raw_values, (str, bytes, bytearray)
            ):
                values = list(raw_values)
            else:
                values = [raw_values]
            if not values:
                clauses.append("0")
                continue
            non_null = [value for value in values if value is not None]
            accepts_null = len(non_null) != len(values)
            parts: list[str] = []
            if non_null:
                parts.append(f'f."{field}" IN ({placeholders(len(non_null))})')
                params.extend(non_null)
            if accepts_null:
                parts.append(f'f."{field}" IS NULL')
            clauses.append(f"({' OR '.join(parts)})")

        for field, operator, value in (
            ("load_value", ">=", load_min),
            ("load_value", "<=", load_max),
            ("speed_ratio", ">=", speed_min),
            ("speed_ratio", "<=", speed_max),
        ):
            if value is not None:
                clauses.append(f'f."{field}" {operator} ?')
                params.append(value)

        where_sql = " AND ".join(clauses) if clauses else "1"
        return where_sql, params

    def filter_conditions(
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
    ) -> list[dict[str, Any]]:
        where_sql, params = self._filter_sql(
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
        sql = f"SELECT f.* FROM files AS f WHERE {where_sql} ORDER BY f.created_at DESC, f.file_uid"
        if limit is not None:
            if limit < 0 or offset < 0:
                raise ValueError("limit and offset must be non-negative")
            sql += " LIMIT ? OFFSET ?"
            params.extend((limit, offset))
        return as_dicts(self.connection.execute(sql, params).fetchall())

    def condition_counts(
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
    ) -> dict[str, int]:
        where_sql, params = self._filter_sql(
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
        row = self.connection.execute(
            f"""
            SELECT
                COUNT(DISTINCT f.file_uid) AS file_count,
                COUNT(s.sample_id) AS sample_count
            FROM files AS f
            LEFT JOIN samples AS s ON s.file_uid = f.file_uid
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        return {"file_count": int(row[0]), "sample_count": int(row[1])}


__all__ = ["CONDITION_FIELDS", "FileRepository"]
