"""Repositories for import runs and per-file import items."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any
from uuid import uuid4

from ._common import apply_updates, as_dict, as_dicts, json_text, placeholders, utc_now


_RUN_FIELDS = frozenset(
    {
        "operation",
        "source_scope",
        "target_storage_id",
        "conditions_json",
        "status",
        "total_count",
        "success_count",
        "skip_count",
        "conflict_count",
        "failed_count",
        "finished_at",
    }
)

_ITEM_FIELDS = frozenset(
    {
        "run_uuid",
        "source_display_path",
        "target_relative_path",
        "action",
        "status",
        "payload_sha256",
        "stored_sha256",
        "error_message",
    }
)


class ImportRunRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        required = {"operation", "source_scope", "target_storage_id"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"missing import run fields: {', '.join(sorted(missing))}")
        unknown = set(values) - (_RUN_FIELDS | {"run_uuid", "started_at"})
        if unknown:
            raise ValueError(f"unsupported import run fields: {', '.join(sorted(unknown))}")
        record = {
            "run_uuid": str(values.get("run_uuid") or uuid4()),
            "operation": values["operation"],
            "source_scope": values["source_scope"],
            "target_storage_id": values["target_storage_id"],
            "conditions_json": json_text(values.get("conditions_json"), default={}),
            "status": values.get("status", "planned"),
            "total_count": values.get("total_count", 0),
            "success_count": values.get("success_count", 0),
            "skip_count": values.get("skip_count", 0),
            "conflict_count": values.get("conflict_count", 0),
            "failed_count": values.get("failed_count", 0),
            "started_at": values.get("started_at", utc_now()),
            "finished_at": values.get("finished_at"),
        }
        columns = tuple(record)
        self.connection.execute(
            f"INSERT INTO import_runs ({', '.join(columns)}) VALUES ({placeholders(len(columns))})",
            tuple(record[column] for column in columns),
        )
        created = self.get(record["run_uuid"])
        assert created is not None
        return created

    def get(self, run_uuid: str) -> dict[str, Any] | None:
        return as_dict(
            self.connection.execute(
                "SELECT * FROM import_runs WHERE run_uuid = ?", (run_uuid,)
            ).fetchone()
        )

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        sql = "SELECT * FROM import_runs"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY started_at DESC, run_uuid LIMIT ?"
        params.append(limit)
        return as_dicts(self.connection.execute(sql, params).fetchall())

    def update(self, run_uuid: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        prepared = dict(values)
        if "conditions_json" in prepared:
            prepared["conditions_json"] = json_text(prepared["conditions_json"], default={})
        changed = apply_updates(
            self.connection,
            table="import_runs",
            key_sql="run_uuid = ?",
            key_params=(run_uuid,),
            values=prepared,
            allowed_fields=_RUN_FIELDS,
            touch_updated_at=False,
        )
        return self.get(run_uuid) if changed else None

    def delete(self, run_uuid: str) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM import_runs WHERE run_uuid = ?", (run_uuid,)
            ).rowcount
        )


class ImportItemRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        required = {"run_uuid", "source_display_path", "action"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"missing import item fields: {', '.join(sorted(missing))}")
        unknown = set(values) - (_ITEM_FIELDS | {"item_uuid", "created_at", "updated_at"})
        if unknown:
            raise ValueError(f"unsupported import item fields: {', '.join(sorted(unknown))}")
        now = utc_now()
        record = {
            "item_uuid": str(values.get("item_uuid") or uuid4()),
            "run_uuid": values["run_uuid"],
            "source_display_path": values["source_display_path"],
            "target_relative_path": values.get("target_relative_path"),
            "action": values["action"],
            "status": values.get("status", "planned"),
            "payload_sha256": values.get("payload_sha256"),
            "stored_sha256": values.get("stored_sha256"),
            "error_message": values.get("error_message"),
            "created_at": values.get("created_at", now),
            "updated_at": values.get("updated_at", now),
        }
        columns = tuple(record)
        self.connection.execute(
            f"INSERT INTO import_items ({', '.join(columns)}) VALUES ({placeholders(len(columns))})",
            tuple(record[column] for column in columns),
        )
        created = self.get(record["item_uuid"])
        assert created is not None
        return created

    def get(self, item_uuid: str) -> dict[str, Any] | None:
        return as_dict(
            self.connection.execute(
                "SELECT * FROM import_items WHERE item_uuid = ?", (item_uuid,)
            ).fetchone()
        )

    def list_by_run(
        self,
        run_uuid: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM import_items WHERE run_uuid = ?"
        params: list[Any] = [run_uuid]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at, item_uuid"
        return as_dicts(self.connection.execute(sql, params).fetchall())

    def update(self, item_uuid: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        changed = apply_updates(
            self.connection,
            table="import_items",
            key_sql="item_uuid = ?",
            key_params=(item_uuid,),
            values=dict(values),
            allowed_fields=_ITEM_FIELDS,
        )
        return self.get(item_uuid) if changed else None

    def delete(self, item_uuid: str) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM import_items WHERE item_uuid = ?", (item_uuid,)
            ).rowcount
        )


class ImportRepository:
    """Convenience facade exposing both run and item repositories."""

    def __init__(self, connection: sqlite3.Connection):
        self.runs = ImportRunRepository(connection)
        self.items = ImportItemRepository(connection)


__all__ = ["ImportItemRepository", "ImportRepository", "ImportRunRepository"]
