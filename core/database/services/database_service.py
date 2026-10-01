"""Creation, opening and transaction lifecycle for AI-3.0 databases."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import os
import sqlite3
from typing import Any
from uuid import uuid4

from ..schema import (
    SCHEMA_VERSION,
    SchemaError,
    configure_connection,
    initialize_schema,
    transaction as schema_transaction,
    validate_schema,
)


@dataclass(frozen=True, slots=True)
class DatabaseInfo:
    path: Path
    database_uid: str
    database_name: str
    default_storage_id: str
    schema_version: int
    storage_root: Path | None = None


class DatabaseService:
    """An independent handle to one AI-3.0 SQLite database file."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()

    @staticmethod
    def list_catalog_records(
        pending: Sequence[Mapping[str, Any]],
        registered: Sequence[Mapping[str, Any]],
        *,
        current_database: str | Path | None = None,
        default_storage_id: str = "wuxi_raw",
    ) -> list[dict[str, Any]]:
        """Describe catalog entries without relying on an HTTP or UI runtime."""
        active = Path(current_database).expanduser().resolve(strict=False) if current_database else None
        records = [dict(item) for item in pending]
        for saved in registered:
            path = Path(str(saved["path"])).expanduser().resolve(strict=False)
            if path.parent.name.casefold() != "databases":
                raise ValueError("database is not inside <database_root>/databases")
            database_root = path.parent.parent
            if database_root.name.casefold() == "_ai3":
                database_root = database_root.parent
            exists = path.is_file()
            records.append({
                **saved,
                "sqlite_path": str(path),
                "database_root": str(saved.get("dataset_root") or database_root),
                "default_storage_id": str(saved.get("storage_id") or default_storage_id),
                "schema_version": None,
                "active": active == path,
                "path_exists": exists,
                "error": None if exists else "数据库文件不存在或路径当前不可访问",
            })
        return records

    @staticmethod
    def database_directory_name(database_name: str) -> str:
        """Return the stable folder name used for a self-contained database."""

        name = str(database_name).strip()
        if name.casefold().endswith(".sqlite3"):
            name = name[: -len(".sqlite3")].rstrip()
        if (
            not name
            or name in {".", ".."}
            or name.startswith(".")
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise ValueError("database_name must be a visible plain folder name")
        return name

    @staticmethod
    def default_database_path(storage_root: str | Path, database_name: str) -> Path:
        name = database_name.strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("database_name must be a plain file name")
        if name.casefold().endswith(".sqlite3"):
            file_name = name
        else:
            file_name = f"{name}.sqlite3"
        return Path(storage_root).expanduser().resolve() / "databases" / file_name

    @staticmethod
    def read_lines(storage_root: str | Path) -> list[str] | None:
        """Read the production-line list stored with a database folder."""
        root = Path(storage_root)
        path = root / "databases" / "lines.json"
        if not path.exists():
            path = root / "lines.json"
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError(f"production-line list must not be a symlink: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"invalid production-line list: {path}")
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @staticmethod
    def write_lines(storage_root: str | Path, lines: Sequence[str]) -> None:
        """Atomically save production lines beside the SQLite file."""
        root = Path(storage_root).resolve(strict=True)
        database_dir = root / "databases"
        if database_dir.is_symlink() or not database_dir.is_dir():
            raise NotADirectoryError(database_dir)
        path = database_dir / "lines.json"
        if path.is_symlink():
            raise ValueError(f"production-line list must not be a symlink: {path}")
        temporary = database_dir / f".lines.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps({"lines": list(dict.fromkeys(lines))}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def migrate_lines(cls, storage_root: str | Path) -> list[str] | None:
        """Move the old root-level list after safely writing its new copy."""
        root = Path(storage_root).resolve(strict=True)
        old_path = root / "lines.json"
        new_path = root / "databases" / "lines.json"
        lines = cls.read_lines(root)
        if lines is not None and old_path.exists() and not new_path.exists():
            cls.write_lines(root, lines)
            old_path.unlink()
        return lines

    @classmethod
    def create_in_parent(
        cls, parent: str | Path, database_name: str, *, storage_id: str | None = None
    ) -> DatabaseInfo:
        """Create a self-contained named folder below an existing parent."""
        name = cls.database_directory_name(database_name)
        parent_path = Path(parent).expanduser().resolve(strict=True)
        if not parent_path.is_dir():
            raise NotADirectoryError(parent_path)
        root = parent_path / name
        root.mkdir()
        try:
            info = cls.create_database(
                database_name=name, storage_root=root, storage_id=storage_id or name,
            )
            cls.write_lines(root, [])
        except BaseException:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
            raise
        return info

    @classmethod
    def create_database(
        cls,
        *,
        database_name: str,
        storage_root: str | Path,
        database_path: str | Path | None = None,
        storage_id: str = "wuxi_raw",
        database_uid: str | None = None,
    ) -> DatabaseInfo:
        """Create an empty schema-v1 database without overwriting an existing file."""

        configured_root = Path(
            os.path.abspath(os.fspath(Path(storage_root).expanduser()))
        )
        configured_parts = configured_root.parts
        if (
            len(configured_parts) >= 3
            and configured_parts[1].casefold() == "volumes"
        ):
            configured_mount = Path(
                configured_parts[0], configured_parts[1], configured_parts[2]
            )
            if not os.path.ismount(configured_mount):
                raise FileNotFoundError(
                    f"storage volume is not mounted: {configured_mount}"
                )
            current = configured_mount
            for part in configured_parts[3:]:
                current = current / part
                if current.is_symlink():
                    raise ValueError(
                        f"storage root must not contain symlinks: {configured_root}"
                    )
        root = configured_root.resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"storage root is not an accessible directory: {root}")
        database_dir = root / "databases"
        if database_dir.is_symlink():
            raise ValueError(f"database management path must not be a symlink: {database_dir}")
        database_dir.mkdir(parents=True, exist_ok=True)
        database_dir = database_dir.resolve()
        try:
            database_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"database management path escapes storage root: {database_dir}") from exc

        for directory_name in ("backups", "exports", "logs"):
            directory = database_dir / directory_name
            if directory.is_symlink():
                raise ValueError(f"database management path must not be a symlink: {directory}")
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"database management path escapes storage root: {directory}") from exc
        target = (
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else cls.default_database_path(root, database_name)
        )
        if target.parent != database_dir:
            raise ValueError(f"database must be created directly in: {database_dir}")

        # Claim the exact path atomically.  This prevents a losing concurrent
        # creator from opening or later deleting a database created by the
        # winner between an exists() check and sqlite3.connect().
        try:
            owner_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(f"database already exists: {target}") from exc
        owner_stat = os.fstat(owner_fd)

        service = cls(target)
        connection: sqlite3.Connection | None = None
        try:
            connection = service.connect(validate=False)
            initialize_schema(
                connection,
                database_uid=str(database_uid or uuid4()),
                database_name=database_name,
                default_storage_id=storage_id,
            )
            meta = validate_schema(connection)
        except BaseException:
            if connection is not None:
                connection.close()
            try:
                current = target.stat()
                still_owned = (
                    current.st_dev == owner_stat.st_dev
                    and current.st_ino == owner_stat.st_ino
                )
            except FileNotFoundError:
                still_owned = False
            if still_owned:
                for suffix in ("-wal", "-shm", ""):
                    candidate = Path(f"{target}{suffix}")
                    if candidate.exists():
                        candidate.unlink()
            raise
        else:
            connection.close()
        finally:
            os.close(owner_fd)

        return DatabaseInfo(
            path=target,
            database_uid=str(meta["database_uid"]),
            database_name=str(meta["database_name"]),
            default_storage_id=str(meta["default_storage_id"]),
            schema_version=int(meta["schema_version"]),
            storage_root=root,
        )

    @classmethod
    def open_database(
        cls,
        database_path: str | Path,
        *,
        storage_root: str | Path | None = None,
        expected_storage_id: str | None = None,
        require_storage: bool = True,
    ) -> DatabaseInfo:
        """Validate and describe an existing schema-v1 database."""

        service = cls(database_path)
        if not service.database_path.is_file():
            raise FileNotFoundError(f"database does not exist: {service.database_path}")
        with closing(service.connect()) as connection:
            meta = validate_schema(connection)

        actual_storage_id = str(meta["default_storage_id"])
        if expected_storage_id is not None and actual_storage_id != expected_storage_id:
            raise SchemaError(
                f"database storage id is {actual_storage_id!r}, expected {expected_storage_id!r}"
            )

        root: Path | None = None
        if storage_root is not None:
            root = Path(storage_root).expanduser().resolve()
            if require_storage and (not root.exists() or not root.is_dir()):
                raise FileNotFoundError(f"storage root is not accessible: {root}")

        return DatabaseInfo(
            path=service.database_path,
            database_uid=str(meta["database_uid"]),
            database_name=str(meta["database_name"]),
            default_storage_id=actual_storage_id,
            schema_version=int(meta["schema_version"]),
            storage_root=root,
        )

    @classmethod
    def resolve_database_uid(
        cls,
        database_uid: str,
        *,
        storage_root: str | Path,
        expected_storage_id: str,
    ) -> DatabaseInfo:
        """Resolve one database UID inside the configured management directory.

        The lookup scans direct ``*.sqlite3`` children of the current
        ``<storage_root>/databases`` layout and the legacy
        ``<storage_root>/_ai3/databases`` layout.  Symlinks and databases with
        an invalid schema or a different storage id are never eligible.
        """

        uid = str(database_uid).strip()
        if not uid or len(uid) > 128 or "\x00" in uid:
            raise ValueError("database_uid is invalid")

        root = Path(storage_root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"storage root is not accessible: {root}")
        match: DatabaseInfo | None = None
        database_dirs = [root / "databases", root / "_ai3" / "databases"]
        candidates: list[Path] = []
        for database_dir in database_dirs:
            if database_dir.is_symlink():
                raise ValueError("database management path must not be a symlink")
            if not database_dir.is_dir():
                continue
            resolved_dir = database_dir.resolve()
            try:
                resolved_dir.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"database management path escapes storage root: {database_dir}"
                ) from exc
            candidates.extend(database_dir.iterdir())
        if not candidates:
            raise FileNotFoundError(f"database directory does not exist below: {root}")

        for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
            if candidate.suffix.casefold() != ".sqlite3" or candidate.is_symlink():
                continue
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if resolved_candidate.parent not in {
                directory.resolve() for directory in database_dirs if directory.is_dir()
            } or not resolved_candidate.is_file():
                continue
            try:
                info = cls.open_database(
                    resolved_candidate,
                    storage_root=root,
                    expected_storage_id=expected_storage_id,
                    require_storage=True,
                )
            except (OSError, sqlite3.DatabaseError, SchemaError):
                continue
            if info.database_uid != uid:
                continue
            if match is not None:
                raise SchemaError(f"database_uid is not unique: {uid}")
            match = info

        if match is None:
            raise FileNotFoundError(f"database_uid was not found: {uid}")
        return match

    def update_database_name(
        self,
        database_name: str,
        *,
        expected_database_uid: str | None = None,
    ) -> DatabaseInfo:
        """Update display metadata without renaming or moving the SQLite file."""

        name = str(database_name).strip()
        if not name or len(name) > 120 or "\x00" in name:
            raise ValueError("database_name is invalid")
        with self.transaction() as connection:
            if expected_database_uid is None:
                cursor = connection.execute(
                    """
                    UPDATE database_meta
                    SET database_name = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (name,),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE database_meta
                    SET database_name = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE database_uid = ?
                    """,
                    (name, expected_database_uid),
                )
            if cursor.rowcount != 1:
                raise SchemaError("database identity changed before the metadata update")
            meta = validate_schema(connection)
        return DatabaseInfo(
            path=self.database_path,
            database_uid=str(meta["database_uid"]),
            database_name=str(meta["database_name"]),
            default_storage_id=str(meta["default_storage_id"]),
            schema_version=int(meta["schema_version"]),
        )

    def connect(
        self,
        *,
        readonly: bool = False,
        validate: bool = True,
    ) -> sqlite3.Connection:
        if validate and not self.database_path.is_file():
            raise FileNotFoundError(f"database does not exist: {self.database_path}")
        if readonly:
            if not self.database_path.is_file():
                raise FileNotFoundError(f"database does not exist: {self.database_path}")
            uri = f"{self.database_path.as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None)
            configure_connection(connection, enable_wal=False)
        else:
            connection = sqlite3.connect(self.database_path, isolation_level=None)
            configure_connection(connection, enable_wal=True)
        try:
            if validate:
                validate_schema(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def validate_schema(self) -> dict[str, Any]:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"database does not exist: {self.database_path}")
        with closing(self.connect(validate=False)) as connection:
            return validate_schema(connection)

    @contextmanager
    def transaction(
        self,
        connection: sqlite3.Connection | None = None,
        *,
        immediate: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        """Open or reuse a connection and provide an atomic transaction."""

        owns_connection = connection is None
        active = connection if connection is not None else self.connect()
        try:
            with schema_transaction(active, immediate=immediate):
                yield active
        finally:
            if owns_connection:
                active.close()


def create_database(**kwargs: Any) -> DatabaseInfo:
    return DatabaseService.create_database(**kwargs)


def open_database(database_path: str | Path, **kwargs: Any) -> DatabaseInfo:
    return DatabaseService.open_database(database_path, **kwargs)


__all__ = [
    "DatabaseInfo",
    "DatabaseService",
    "SCHEMA_VERSION",
    "create_database",
    "open_database",
]
