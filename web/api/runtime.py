"""Small process-local runtime state.

Long-lived facts live in the selected SQLite database.  This object only keeps
the database currently opened by this desktop process and short-lived scan
previews that must be explicitly confirmed before they can be applied.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import RLock
from typing import Any

from core.files import StorageRoots, StorageUnavailableError


PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
USER_DATA_ROOT = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AI-3.0"
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT / "data"
)
DEFAULT_STORAGE_CONFIG = PROJECT_ROOT / "config" / "storage_roots.json"
DEFAULT_PROFILES_DIR = PROJECT_ROOT / "config" / "sample_profiles"
DEFAULT_DATABASE_ROOT = USER_DATA_ROOT
DEFAULT_CATALOG_STATE = USER_DATA_ROOT / "catalog_state.json"


class RuntimeState:
    def __init__(self) -> None:
        config_override = os.environ.get("AI3_STORAGE_CONFIG", "").strip()
        config_path = Path(config_override or DEFAULT_STORAGE_CONFIG).expanduser()
        root_override = os.environ.get("AI3_STORAGE_ROOT", "").strip()
        if root_override:
            configured_roots = {"wuxi_raw": root_override}
        elif config_override:
            configured_roots = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            # Interactive startup deliberately has no implicit disk.  The
            # user selects the data folder from the database home page.
            configured_roots = {}
        if not isinstance(configured_roots, dict):
            raise RuntimeError(f"Invalid storage root configuration: {config_path}")
        self.config_path = config_path.resolve()
        normalized_roots = {
            str(storage_id): Path(str(root)).expanduser()
            for storage_id, root in configured_roots.items()
        }
        # An explicit override is primarily used by tests and controlled
        # deployments, so retain its historical behaviour.  A configured
        # default, however, is only selected when it is actually available.
        # This keeps a missing external volume from becoming an implicit write
        # target under /Volumes on startup.
        if root_override:
            self.storage_roots = normalized_roots
        elif config_override:
            self.storage_roots = self._available_configured_roots(normalized_roots)
        else:
            self.storage_roots = {}
        self.profiles_dir = Path(
            os.environ.get("AI3_SAMPLE_PROFILES", str(DEFAULT_PROFILES_DIR))
        ).expanduser()
        database_root = Path(
            os.environ.get("AI3_DATABASE_ROOT", str(DEFAULT_DATABASE_ROOT))
        ).expanduser()
        if not database_root.is_absolute():
            database_root = Path.cwd() / database_root
        # A catalog root is where SQLite database files are managed.  It is
        # intentionally independent from the optional raw-data storage root.
        default_database_root = database_root.resolve(strict=False)
        catalog_state_path = Path(
            os.environ.get("AI3_CATALOG_STATE", str(DEFAULT_CATALOG_STATE))
        ).expanduser()
        if not catalog_state_path.is_absolute():
            catalog_state_path = Path.cwd() / catalog_state_path
        self.catalog_state_path = catalog_state_path.resolve(strict=False)
        self.database_roots = self._load_database_roots(default_database_root)
        self.hidden_database_paths = self._load_hidden_database_paths()
        self.pending_databases = self._load_pending_databases()
        self.registered_databases = self._load_registered_databases()
        self.current_database: Path | None = None
        self.current_pending_database_uid: str | None = None
        self.previews: dict[str, dict[str, Any]] = {}
        self.lock = RLock()

    def _load_database_roots(self, default_root: Path) -> list[Path]:
        roots = [default_root]
        try:
            payload = json.loads(self.catalog_state_path.read_text(encoding="utf-8"))
            values = payload.get("database_roots", [])
            if not isinstance(values, list):
                return roots
            for value in values:
                text = str(value).strip()
                if not text:
                    continue
                root = Path(text).expanduser().resolve(strict=False)
                if root not in roots:
                    roots.append(root)
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return roots

    def _load_hidden_database_paths(self) -> set[Path]:
        try:
            payload = json.loads(self.catalog_state_path.read_text(encoding="utf-8"))
            values = payload.get("hidden_database_paths", [])
            if not isinstance(values, list):
                return set()
            return {
                Path(str(value)).expanduser().resolve(strict=False)
                for value in values
                if str(value).strip()
            }
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return set()

    def _load_pending_databases(self) -> dict[str, dict[str, str]]:
        try:
            payload = json.loads(self.catalog_state_path.read_text(encoding="utf-8"))
            values = payload.get("pending_databases", [])
            if not isinstance(values, list):
                return {}
            records: dict[str, dict[str, str]] = {}
            for value in values:
                if not isinstance(value, dict):
                    continue
                database_uid = str(value.get("database_uid") or "").strip()
                database_name = str(value.get("database_name") or "").strip()
                if database_uid and database_name:
                    records[database_uid] = {
                        "database_uid": database_uid,
                        "database_name": database_name,
                    }
            return records
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _load_registered_databases(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.catalog_state_path.read_text(encoding="utf-8"))
            values = payload.get("databases", [])
            if not isinstance(values, list):
                return {}
            records: dict[str, dict[str, str]] = {}
            for value in values:
                if not isinstance(value, dict):
                    continue
                database_uid = str(value.get("database_uid") or "").strip()
                database_name = str(value.get("database_name") or "").strip()
                path = str(value.get("path") or "").strip()
                if database_uid and database_name and path:
                    dataset_root = str(value.get("dataset_root") or "").strip()
                    if not dataset_root:
                        target = Path(path).expanduser().resolve(strict=False)
                        dataset_root = str(target.parent.parent) if target.parent.name.casefold() == "databases" else ""
                    storage_id = str(value.get("storage_id") or "wuxi_raw").strip()
                    raw_lines = value.get("lines", [])
                    lines = [] if not isinstance(raw_lines, list) else list(dict.fromkeys(
                        str(item).strip() for item in raw_lines if str(item).strip()
                    ))
                    records[database_uid] = {
                        "database_uid": database_uid,
                        "database_name": database_name,
                        "path": path,
                        "sqlite_path": str(value.get("sqlite_path") or path),
                        "dataset_root": dataset_root,
                        "storage_id": storage_id,
                        "lines": lines,
                    }
            return records
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _save_catalog_state_locked(self) -> None:
        self.catalog_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog_state_path.with_name(
            f".{self.catalog_state_path.name}.{os.getpid()}.tmp"
        )
        payload = {
            "database_roots": sorted(str(path) for path in self.database_roots),
            "hidden_database_paths": sorted(str(path) for path in self.hidden_database_paths),
            "pending_databases": sorted(
                self.pending_databases.values(),
                key=lambda item: (item["database_name"].casefold(), item["database_uid"]),
            ),
            "databases": sorted(
                self.registered_databases.values(),
                key=lambda item: (item["database_name"].casefold(), item["database_uid"]),
            ),
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.catalog_state_path)

    def list_registered_databases(self) -> list[dict[str, str]]:
        """Reload and return only database entries explicitly saved in the catalog."""

        with self.lock:
            self.registered_databases = self._load_registered_databases()
            return [dict(record) for record in self.registered_databases.values()]

    def register_database(
        self,
        database_uid: str,
        database_name: str,
        path: str | Path,
        *,
        storage_id: str | None = None,
        dataset_root: str | Path | None = None,
    ) -> None:
        resolved = Path(path).expanduser().resolve(strict=False)
        with self.lock:
            self.registered_databases = self._load_registered_databases()
            existing = self.registered_databases.get(str(database_uid), {})
            resolved_root = Path(
                dataset_root
                if dataset_root is not None
                else existing.get("dataset_root") or resolved.parent.parent
            ).expanduser().resolve(strict=False)
            resolved_storage_id = str(
                storage_id or existing.get("storage_id") or "wuxi_raw"
            )
            for uid, record in self.registered_databases.items():
                if uid == str(database_uid):
                    continue
                record_root = Path(
                    record.get("dataset_root") or Path(record["path"]).parent.parent
                ).expanduser().resolve(strict=False)
                if record_root == resolved_root:
                    raise FileExistsError(f"dataset root already registered: {resolved_root}")
                if str(record.get("database_name") or "").casefold() == str(database_name).casefold():
                    raise FileExistsError(f"database name already registered: {database_name}")
                if str(record.get("storage_id") or "wuxi_raw").casefold() == resolved_storage_id.casefold():
                    raise FileExistsError(f"storage_id already registered: {resolved_storage_id}")
            self.registered_databases[str(database_uid)] = {
                "database_uid": str(database_uid),
                "database_name": str(database_name),
                "path": str(resolved),
                "sqlite_path": str(resolved),
                "dataset_root": str(resolved_root),
                "storage_id": resolved_storage_id,
                "lines": list(existing.get("lines", [])),
            }
            self._save_catalog_state_locked()

    def add_database_line(self, database_uid: str, line: str) -> list[str]:
        with self.lock:
            self.registered_databases = self._load_registered_databases()
            record = self.registered_databases.get(str(database_uid))
            if record is None:
                raise KeyError(f"database was not found: {database_uid}")
            lines = list(record.get("lines", []))
            if line.casefold() not in {item.casefold() for item in lines}:
                lines.append(line)
                lines.sort(key=str.casefold)
                record["lines"] = lines
                self._save_catalog_state_locked()
            return list(lines)

    def unregister_database(self, database_uid: str) -> dict[str, str] | None:
        with self.lock:
            self.registered_databases = self._load_registered_databases()
            record = self.registered_databases.pop(str(database_uid), None)
            if record is not None:
                removed_path = Path(record["path"]).expanduser().resolve(strict=False)
                if self.current_database == removed_path:
                    self.current_database = None
                    self.current_pending_database_uid = None
                    self.storage_roots = {}
                    self.previews.clear()
                self._save_catalog_state_locked()
            return dict(record) if record is not None else None

    def is_database_hidden(self, path: str | Path) -> bool:
        resolved = Path(path).expanduser().resolve(strict=False)
        with self.lock:
            return resolved in self.hidden_database_paths

    def hide_database(self, path: str | Path) -> Path:
        """Remove one database from the home list without deleting its files."""

        resolved = Path(path).expanduser().resolve(strict=False)
        with self.lock:
            self.hidden_database_paths.add(resolved)
            if self.current_database == resolved:
                self.current_database = None
                self.current_pending_database_uid = None
                self.storage_roots = {}
                self.previews.clear()
            self._save_catalog_state_locked()
        return resolved

    def list_pending_databases(self) -> list[dict[str, Any]]:
        with self.lock:
            self.pending_databases = self._load_pending_databases()
            return [
                {
                    **record,
                    "default_storage_id": "wuxi_raw",
                    "schema_version": None,
                    "path": None,
                    "sqlite_path": None,
                    "database_root": None,
                    "pending_path": True,
                    "active": record["database_uid"] == self.current_pending_database_uid,
                }
                for record in sorted(
                    self.pending_databases.values(),
                    key=lambda item: (item["database_name"].casefold(), item["database_uid"]),
                )
            ]

    def create_pending_database(self, database_uid: str, database_name: str) -> dict[str, Any]:
        record = {
            "database_uid": str(database_uid),
            "database_name": str(database_name),
        }
        with self.lock:
            self.pending_databases[record["database_uid"]] = record
            self.current_pending_database_uid = record["database_uid"]
            self.current_database = None
            self.storage_roots = {}
            self.previews.clear()
            self._save_catalog_state_locked()
        return {**record, "pending_path": True}

    def pending_database(self, database_uid: str) -> dict[str, str] | None:
        with self.lock:
            record = self.pending_databases.get(str(database_uid))
            return dict(record) if record is not None else None

    def activate_pending_database(self, database_uid: str) -> dict[str, str]:
        with self.lock:
            record = self.pending_databases.get(str(database_uid))
            if record is None:
                raise KeyError(f"pending database was not found: {database_uid}")
            self.current_pending_database_uid = str(database_uid)
            self.current_database = None
            self.storage_roots = {}
            self.previews.clear()
            return dict(record)

    def update_pending_database(self, database_uid: str, database_name: str) -> dict[str, str]:
        with self.lock:
            record = self.pending_databases.get(str(database_uid))
            if record is None:
                raise KeyError(f"pending database was not found: {database_uid}")
            record["database_name"] = str(database_name)
            self._save_catalog_state_locked()
            return dict(record)

    def remove_pending_database(self, database_uid: str) -> dict[str, str] | None:
        with self.lock:
            record = self.pending_databases.pop(str(database_uid), None)
            if record is not None:
                if self.current_pending_database_uid == str(database_uid):
                    self.current_pending_database_uid = None
                    self.current_database = None
                    self.storage_roots = {}
                    self.previews.clear()
                self._save_catalog_state_locked()
            return dict(record) if record is not None else None

    def unhide_database(self, path: str | Path) -> Path:
        """Restore a previously removed home-list record after an explicit open."""

        resolved = Path(path).expanduser().resolve(strict=False)
        with self.lock:
            if resolved in self.hidden_database_paths:
                self.hidden_database_paths.remove(resolved)
                self._save_catalog_state_locked()
        return resolved

    @staticmethod
    def _available_configured_roots(roots: dict[str, Path]) -> dict[str, Path]:
        available: dict[str, Path] = {}
        try:
            configured = StorageRoots(roots)
        except (TypeError, ValueError):
            return available
        for storage_id, root in roots.items():
            try:
                configured.root(storage_id, require_available=True)
            except (OSError, StorageUnavailableError, ValueError):
                continue
            available[storage_id] = root
        return available

    def storage_root(self, storage_id: str = "wuxi_raw") -> Path | None:
        with self.lock:
            root = self.storage_roots.get(storage_id)
            return Path(root) if root is not None else None

    def set_storage_root(
        self, path: str | Path, *, storage_id: str = "wuxi_raw"
    ) -> Path:
        """Select an accessible storage root and invalidate root-bound state."""

        candidate_roots = {storage_id: Path(path).expanduser()}
        root = StorageRoots(candidate_roots).root(
            storage_id, require_available=True
        )
        with self.lock:
            current = self.storage_roots.get(storage_id)
            changed = current is None or current.resolve(strict=False) != root
            self.storage_roots = {storage_id: root}
            if changed:
                self.previews.clear()
        return root

    def clear_storage_root(self) -> None:
        with self.lock:
            self.storage_roots = {}
            self.previews.clear()

    def catalog_roots(self) -> tuple[Path, ...]:
        with self.lock:
            return tuple(self.database_roots)

    def add_database_root(self, path: str | Path) -> Path:
        """Add one validated database catalog without changing data storage."""

        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.is_symlink():
            raise ValueError(f"database catalog root must not be a symlink: {candidate}")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(
                f"database catalog root is not a directory: {resolved}"
            )
        with self.lock:
            if resolved not in self.database_roots:
                self.database_roots.append(resolved)
                self._save_catalog_state_locked()
        return resolved

    def replace_database_root(
        self, old_path: str | Path, new_path: str | Path
    ) -> Path:
        """Replace an explicitly tracked root after a verified migration."""

        old = Path(old_path).expanduser().resolve(strict=False)
        new = Path(new_path).expanduser().resolve(strict=True)
        with self.lock:
            self.database_roots = [
                root for root in self.database_roots if root.resolve(strict=False) != old
            ]
            if new not in self.database_roots:
                self.database_roots.append(new)
            self._save_catalog_state_locked()
        return new

    def activate_database(
        self,
        path: str | Path,
        *,
        database_root: str | Path,
        storage_id: str = "wuxi_raw",
    ) -> Path:
        """Open one database and bind its own root as its raw-data root.

        AI-3.0 databases are self-contained: raw files live below
        ``database_root`` and SQLite lives below its ``_ai3`` management
        directory.  Activating the pair in one locked operation prevents a
        previous database's storage root from leaking into the new database.
        """

        resolved_database = Path(path).expanduser().resolve(strict=True)
        root = StorageRoots({storage_id: Path(database_root).expanduser()}).root(
            storage_id, require_available=True
        )
        with self.lock:
            catalog_changed = False
            if (
                self.current_database != resolved_database
                or self.storage_roots.get(storage_id) != root
            ):
                self.previews.clear()
            self.current_database = resolved_database
            self.current_pending_database_uid = None
            self.storage_roots = {storage_id: root}
            if root not in self.database_roots:
                self.database_roots.append(root)
                catalog_changed = True
            if resolved_database in self.hidden_database_paths:
                self.hidden_database_paths.remove(resolved_database)
                catalog_changed = True
            if catalog_changed:
                self._save_catalog_state_locked()
        return resolved_database

    def set_database(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        with self.lock:
            if self.current_database != resolved:
                self.previews.clear()
                # Data roots are database-specific runtime choices.  Never
                # carry one database's raw-data target into another database.
                # Keep a controlled startup override for the first database;
                # once a database is active, every cross-database switch
                # returns to an explicitly unconfigured data root.
                if self.current_database is not None:
                    self.storage_roots = {}
            self.current_database = resolved
            self.current_pending_database_uid = None
        return resolved

    def require_database(self) -> Path:
        with self.lock:
            if self.current_database is None:
                raise RuntimeError("请先新建或打开数据库")
            return self.current_database

    def put_preview(self, preview: dict[str, Any]) -> str:
        preview_id = str(preview["preview_id"])
        with self.lock:
            # Only the newest 20 previews are useful; previews are immutable.
            self.previews[preview_id] = preview
            while len(self.previews) > 20:
                oldest = next(iter(self.previews))
                self.previews.pop(oldest, None)
        return preview_id

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        with self.lock:
            try:
                return self.previews[preview_id]
            except KeyError as exc:
                raise KeyError("扫描预览不存在或已失效，请重新扫描") from exc

    def discard_preview(self, preview_id: str) -> None:
        with self.lock:
            self.previews.pop(preview_id, None)


state = RuntimeState()
