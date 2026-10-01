"""SQLite schema and connection helpers for AI-3.0.

This module deliberately depends only on Python's standard library.  It owns
the schema contract and the transaction primitive used by repositories and
services; it has no dependency on AI-2.0.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
from typing import Any
import unicodedata
from uuid import uuid4


SCHEMA_VERSION = 1
IMPORT_SOURCE_SUFFIXES = (".tdms", ".tdms.zst", ".wav")
REGISTERED_SUFFIXES = (".tdms.zst", ".wav")

REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "database_meta": frozenset(
        {
            "database_uid",
            "database_name",
            "default_storage_id",
            "import_source_suffixes_json",
            "registered_suffixes_json",
            "schema_version",
            "created_at",
            "updated_at",
        }
    ),
    "files": frozenset(
        {
            "file_uid",
            "storage_id",
            "relative_path",
            "line",
            "device_id",
            "model_name",
            "reference",
            "load_value",
            "load_unit",
            "speed_ratio",
            "acquired_at",
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
            "created_at",
            "updated_at",
        }
    ),
    "samples": frozenset(
        {
            "file_uid",
            "sample_id",
            "sample_scope",
            "display_name",
            "locator_json",
            "sampling_rate_hz",
            "duration_s",
            "sort_order",
            "availability_status",
            "metadata_json",
            "created_at",
            "updated_at",
        }
    ),
    "file_records": frozenset(
        {"file_uid", "storage_id", "relative_path", "conditions", "metadata"}
    ),
    "sample_records": frozenset(
        {"file_uid", "sample_id", "sample_scope"}
    ),
    "label_events": frozenset(
        {
            "event_uuid", "file_uid", "sample_id", "source",
            "result_key", "result_id", "result_name", "result_confidence",
            "reason_key", "reason_id", "reason_name", "reason_confidence",
            "timestamp", "note",
        }
    ),
    "import_runs": frozenset(
        {
            "run_uuid",
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
            "started_at",
            "finished_at",
        }
    ),
    "import_items": frozenset(
        {
            "item_uuid",
            "run_uuid",
            "source_display_path",
            "target_relative_path",
            "action",
            "status",
            "payload_sha256",
            "stored_sha256",
            "error_message",
            "created_at",
            "updated_at",
        }
    ),
}


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE database_meta (
        database_uid TEXT PRIMARY KEY,
        database_name TEXT NOT NULL CHECK (length(trim(database_name)) > 0),
        default_storage_id TEXT NOT NULL CHECK (length(trim(default_storage_id)) > 0),
        import_source_suffixes_json TEXT NOT NULL,
        registered_suffixes_json TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    "CREATE UNIQUE INDEX uq_database_meta_singleton ON database_meta(schema_version)",
    """
    CREATE TABLE files (
        file_uid TEXT PRIMARY KEY,
        storage_id TEXT NOT NULL CHECK (length(trim(storage_id)) > 0),
        relative_path TEXT NOT NULL
            CHECK (length(relative_path) > 0)
            CHECK (substr(relative_path, 1, 1) <> '/')
            CHECK (
                relative_path <> '..'
                AND relative_path NOT LIKE '../%'
                AND relative_path NOT LIKE '%/../%'
                AND relative_path NOT LIKE '%/..'
            )
            CHECK (
                lower(relative_path) GLOB '*.tdms.zst'
                OR lower(relative_path) GLOB '*.wav'
            ),

        line TEXT,
        device_id TEXT,
        model_name TEXT,
        reference TEXT,
        load_value REAL,
        load_unit TEXT,
        speed_ratio REAL,
        acquired_at TEXT,

        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) > 0),
        stored_sha256 TEXT NOT NULL CHECK (length(stored_sha256) > 0),
        payload_size_bytes INTEGER NOT NULL CHECK (payload_size_bytes >= 0),
        stored_size_bytes INTEGER NOT NULL CHECK (stored_size_bytes >= 0),
        stored_mtime_ns INTEGER NOT NULL CHECK (stored_mtime_ns >= 0),

        record_status TEXT NOT NULL DEFAULT 'active'
            CHECK (record_status IN ('active', 'superseded', 'archived')),
        availability_status TEXT NOT NULL DEFAULT 'present'
            CHECK (availability_status IN ('present', 'missing')),
        integrity_status TEXT NOT NULL DEFAULT 'verified'
            CHECK (integrity_status IN ('verified', 'changed', 'unreadable')),
        last_seen_at TEXT,
        last_verified_at TEXT,

        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE UNIQUE INDEX uq_files_active_storage_path
    ON files(storage_id, relative_path COLLATE AI3_PATH)
    WHERE record_status = 'active'
    """,
    "CREATE INDEX ix_files_payload_sha256 ON files(payload_sha256)",
    "CREATE INDEX ix_files_conditions ON files(line, device_id, model_name, reference)",
    """
    CREATE TABLE samples (
        file_uid TEXT NOT NULL,
        sample_id TEXT NOT NULL CHECK (length(trim(sample_id)) > 0),
        sample_scope TEXT NOT NULL CHECK (sample_scope IN ('channel', 'whole_file')),
        display_name TEXT NOT NULL,
        locator_json TEXT NOT NULL,
        sampling_rate_hz REAL CHECK (sampling_rate_hz IS NULL OR sampling_rate_hz > 0),
        duration_s REAL CHECK (duration_s IS NULL OR duration_s >= 0),
        sort_order INTEGER NOT NULL DEFAULT 0,
        availability_status TEXT NOT NULL DEFAULT 'present'
            CHECK (availability_status IN ('present', 'missing')),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (file_uid, sample_id),
        FOREIGN KEY (file_uid) REFERENCES files(file_uid)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX ix_samples_file_sort ON samples(file_uid, sort_order, sample_id)",
    """
    CREATE TABLE file_records (
        file_uid TEXT PRIMARY KEY,
        storage_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        conditions TEXT NOT NULL DEFAULT '{}',
        metadata TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (file_uid) REFERENCES files(file_uid)
            ON UPDATE CASCADE ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE sample_records (
        file_uid TEXT NOT NULL,
        sample_id TEXT NOT NULL,
        sample_scope TEXT NOT NULL,
        PRIMARY KEY (file_uid, sample_id),
        FOREIGN KEY (file_uid, sample_id) REFERENCES samples(file_uid, sample_id)
            ON UPDATE CASCADE ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE label_events (
        event_uuid TEXT PRIMARY KEY,
        file_uid TEXT NOT NULL,
        sample_id TEXT NOT NULL,
        source TEXT NOT NULL,
        result_key TEXT,
        result_id INTEGER,
        result_name TEXT,
        result_confidence REAL,
        reason_key TEXT,
        reason_id INTEGER,
        reason_name TEXT,
        reason_confidence REAL,
        timestamp TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (file_uid, sample_id) REFERENCES sample_records(file_uid, sample_id)
            ON UPDATE CASCADE ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_label_events_sample ON label_events(file_uid, sample_id, timestamp)",
    """
    CREATE TABLE import_runs (
        run_uuid TEXT PRIMARY KEY,
        operation TEXT NOT NULL CHECK (length(trim(operation)) > 0),
        source_scope TEXT NOT NULL CHECK (source_scope IN ('inside', 'outside')),
        target_storage_id TEXT NOT NULL CHECK (length(trim(target_storage_id)) > 0),
        conditions_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK (length(trim(status)) > 0),
        total_count INTEGER NOT NULL DEFAULT 0 CHECK (total_count >= 0),
        success_count INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
        skip_count INTEGER NOT NULL DEFAULT 0 CHECK (skip_count >= 0),
        conflict_count INTEGER NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
        failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        finished_at TEXT
    )
    """,
    "CREATE INDEX ix_import_runs_started ON import_runs(started_at DESC)",
    """
    CREATE TABLE import_items (
        item_uuid TEXT PRIMARY KEY,
        run_uuid TEXT NOT NULL,
        source_display_path TEXT NOT NULL,
        target_relative_path TEXT,
        action TEXT NOT NULL CHECK (action IN ('register', 'copy', 'compress')),
        status TEXT NOT NULL CHECK (length(trim(status)) > 0),
        payload_sha256 TEXT,
        stored_sha256 TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (run_uuid) REFERENCES import_runs(run_uuid)
            ON UPDATE CASCADE ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_import_items_run_status ON import_items(run_uuid, status)",
)


class SchemaError(RuntimeError):
    """Raised when a database is not a valid AI-3.0 schema v1 database."""


def configure_connection(
    connection: sqlite3.Connection,
    *,
    enable_wal: bool = True,
    busy_timeout_ms: int = 5_000,
) -> None:
    """Apply the connection settings required by the AI-3.0 database."""

    def compare_paths(left: str, right: str) -> int:
        left_key = unicodedata.normalize("NFC", left).casefold()
        right_key = unicodedata.normalize("NFC", right).casefold()
        return (left_key > right_key) - (left_key < right_key)

    connection.create_collation("AI3_PATH", compare_paths)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if enable_wal:
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise SchemaError(f"cannot enable WAL journal mode (got {journal_mode!r})")
        connection.execute("PRAGMA synchronous = NORMAL")


@contextmanager
def transaction(
    connection: sqlite3.Connection,
    *,
    immediate: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Run an atomic transaction, using a savepoint when already nested."""

    if connection.in_transaction:
        savepoint = f"ai3_{uuid4().hex}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield connection
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return

    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def initialize_schema(
    connection: sqlite3.Connection,
    *,
    database_uid: str,
    database_name: str,
    default_storage_id: str,
) -> None:
    """Create schema v1 and its single metadata row atomically."""

    if not database_uid.strip():
        raise ValueError("database_uid must not be empty")
    if not database_name.strip():
        raise ValueError("database_name must not be empty")
    if not default_storage_id.strip():
        raise ValueError("default_storage_id must not be empty")

    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'database_meta'"
    ).fetchone()
    if existing is not None:
        raise SchemaError("database schema already exists")

    with transaction(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO database_meta (
                database_uid,
                database_name,
                default_storage_id,
                import_source_suffixes_json,
                registered_suffixes_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                database_uid,
                database_name.strip(),
                default_storage_id.strip(),
                json.dumps(IMPORT_SOURCE_SUFFIXES, ensure_ascii=False),
                json.dumps(REGISTERED_SUFFIXES, ensure_ascii=False),
                SCHEMA_VERSION,
            ),
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return frozenset(str(row[1]) for row in rows)


def validate_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    """Validate schema v1 and return the database metadata row."""

    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != SCHEMA_VERSION:
        raise SchemaError(
            f"unsupported schema version {user_version}; expected {SCHEMA_VERSION}"
        )

    for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
        actual_columns = _table_columns(connection, table)
        missing = required_columns - actual_columns
        if missing:
            raise SchemaError(
                f"table {table!r} is missing columns: {', '.join(sorted(missing))}"
            )

    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise SchemaError("SQLite foreign key enforcement is not enabled")

    database_rows = connection.execute("PRAGMA database_list").fetchall()
    main_filename = next((str(row[2]) for row in database_rows if row[1] == "main"), "")
    if main_filename:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise SchemaError(f"database journal mode is {journal_mode!r}, expected 'wal'")

    meta_rows = connection.execute("SELECT * FROM database_meta").fetchall()
    if len(meta_rows) != 1:
        raise SchemaError(f"database_meta must contain exactly one row, got {len(meta_rows)}")
    meta = dict(meta_rows[0])
    if int(meta["schema_version"]) != SCHEMA_VERSION:
        raise SchemaError("database_meta schema_version does not match schema v1")

    try:
        import_suffixes = tuple(json.loads(meta["import_source_suffixes_json"]))
        registered_suffixes = tuple(json.loads(meta["registered_suffixes_json"]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SchemaError("database suffix metadata is not valid JSON") from exc
    if import_suffixes != IMPORT_SOURCE_SUFFIXES:
        raise SchemaError("import source suffix metadata does not match schema v1")
    if registered_suffixes != REGISTERED_SUFFIXES:
        raise SchemaError("registered suffix metadata does not match schema v1")

    index_row = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND name = 'uq_files_active_storage_path'
        """
    ).fetchone()
    index_sql = str(index_row[0]).lower() if index_row is not None else ""
    if "where" not in index_sql or "collate ai3_path" not in index_sql:
        raise SchemaError("active file path partial unique index is missing")
    index_columns = tuple(
        str(row[2])
        for row in connection.execute(
            "PRAGMA index_info('uq_files_active_storage_path')"
        ).fetchall()
    )
    if index_columns != ("storage_id", "relative_path"):
        raise SchemaError("active file path index has the wrong columns")

    files_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'files'"
    ).fetchone()
    files_sql = str(files_sql_row[0]).lower() if files_sql_row is not None else ""
    if ".tdms.zst" not in files_sql or ".wav" not in files_sql:
        raise SchemaError("registered file suffix CHECK constraint is missing")

    sample_foreign_keys = connection.execute("PRAGMA foreign_key_list('samples')").fetchall()
    if not any(row[2] == "files" and row[3] == "file_uid" and row[4] == "file_uid" for row in sample_foreign_keys):
        raise SchemaError("samples.file_uid foreign key is missing")
    item_foreign_keys = connection.execute("PRAGMA foreign_key_list('import_items')").fetchall()
    if not any(row[2] == "import_runs" and row[3] == "run_uuid" and row[4] == "run_uuid" for row in item_foreign_keys):
        raise SchemaError("import_items.run_uuid foreign key is missing")

    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise SchemaError(f"foreign key violations found: {len(foreign_key_errors)}")

    return meta


__all__ = [
    "IMPORT_SOURCE_SUFFIXES",
    "REGISTERED_SUFFIXES",
    "SCHEMA_VERSION",
    "SchemaError",
    "configure_connection",
    "initialize_schema",
    "transaction",
    "validate_schema",
]
