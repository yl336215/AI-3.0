"""Storage-root configuration and safe relative-path handling.

Database rows store only ``storage_id`` plus a canonical POSIX relative path.
All path joins go through this module so an absolute path, ``..`` segment, or
symlink cannot escape the configured storage root.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


DEFAULT_STORAGE_ID = "wuxi_raw"
DEFAULT_STORAGE_ROOTS: dict[str, str] = {}


class StorageError(ValueError):
    """Base class for storage configuration and path errors."""


class UnknownStorageError(StorageError):
    """Raised when a storage id is not configured."""


class InvalidRelativePathError(StorageError):
    """Raised when a database relative path is unsafe or non-canonical."""


class StorageEscapeError(StorageError):
    """Raised when a resolved path escapes its configured storage root."""


class StorageUnavailableError(StorageError):
    """Raised when a configured storage root is not currently accessible."""


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "storage_roots.json"


def normalize_relative_path(value: str | os.PathLike[str], *, allow_empty: bool = False) -> str:
    """Return a canonical POSIX relative path and reject traversal.

    Backslashes are treated as separators so paths cannot bypass validation
    when a database is moved between Windows and POSIX systems.
    """

    raw = unicodedata.normalize("NFC", os.fspath(value))
    if "\x00" in raw:
        raise InvalidRelativePathError("relative_path contains a NUL byte")
    if not raw:
        if allow_empty:
            return ""
        raise InvalidRelativePathError("relative_path must not be empty")
    if Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        raise InvalidRelativePathError(f"relative_path must not be absolute: {raw}")

    text = raw.replace("\\", "/")
    if text.startswith("/"):
        raise InvalidRelativePathError(f"relative_path must not be absolute: {raw}")
    raw_parts = text.split("/")
    if any(part in {".", ".."} for part in raw_parts):
        raise InvalidRelativePathError(f"relative_path contains a forbidden segment: {raw}")

    # Empty segments from repeated separators are normalized away.
    parts = tuple(part for part in raw_parts if part)
    if not parts:
        if allow_empty:
            return ""
        raise InvalidRelativePathError("relative_path must name a file or directory")
    if any(part in {".", ".."} for part in parts):
        raise InvalidRelativePathError(f"relative_path contains a forbidden segment: {raw}")
    return PurePosixPath(*parts).as_posix()


class StorageRoots:
    """Immutable view of configured storage ids and absolute roots."""

    def __init__(self, roots: Mapping[str, str | os.PathLike[str]]):
        normalized: dict[str, Path] = {}
        configured: dict[str, Path] = {}
        for storage_id, root_value in roots.items():
            key = str(storage_id).strip()
            if not key or "/" in key or "\\" in key or key in {".", ".."}:
                raise StorageError(f"invalid storage_id: {storage_id!r}")
            root_text = os.fspath(root_value).strip()
            if not root_text:
                raise StorageError(f"storage root is empty: {key}")
            expanded = Path(root_text).expanduser()
            lexical = Path(os.path.abspath(os.fspath(expanded)))
            configured[key] = lexical
            normalized[key] = lexical.resolve(strict=False)
        if not normalized:
            raise StorageError("at least one storage root is required")
        self._roots = normalized
        self._configured_roots = configured

    @classmethod
    def from_json(cls, path: str | os.PathLike[str]) -> "StorageRoots":
        config_path = Path(path).expanduser()
        payload: Any = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise StorageError("storage_roots.json must contain an object")
        return cls(payload)

    def as_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in self._configured_roots.items()}

    def ids(self) -> tuple[str, ...]:
        return tuple(self._roots)

    def root(self, storage_id: str = DEFAULT_STORAGE_ID, *, require_available: bool = False) -> Path:
        try:
            root = self._roots[storage_id]
            configured_root = self._configured_roots[storage_id]
        except KeyError as exc:
            raise UnknownStorageError(f"unknown storage_id: {storage_id}") from exc
        if require_available:
            # On macOS an unmounted external volume can leave a plain directory
            # under /Volumes.  Treat that directory as unavailable so AI-3.0
            # never starts writing raw data onto the system disk by accident.
            parts = configured_root.parts
            if len(parts) >= 3 and parts[1].casefold() == "volumes":
                mount_point = Path(parts[0], parts[1], parts[2])
                if not os.path.ismount(mount_point):
                    raise StorageUnavailableError(
                        f"storage volume is not mounted: {mount_point}"
                    )
                current = mount_point
                for part in parts[3:]:
                    current = current / part
                    if current.is_symlink():
                        raise StorageUnavailableError(
                            f"storage root must not contain symlinks: {configured_root}"
                        )
            if not root.is_dir():
                raise StorageUnavailableError(f"storage root is unavailable: {configured_root}")
        return root

    def resolve(
        self,
        storage_id: str,
        relative_path: str | os.PathLike[str],
        *,
        require_exists: bool = False,
    ) -> Path:
        root = self.root(storage_id)
        relative = normalize_relative_path(relative_path)
        lexical = root / Path(*PurePosixPath(relative).parts)
        current = root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise StorageEscapeError(
                    f"symlink components are not allowed in storage paths: {lexical}"
                )
        candidate = lexical.resolve(strict=False)
        self._assert_within(root, candidate)
        if require_exists and not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate

    def relative_path(self, storage_id: str, absolute_path: str | os.PathLike[str]) -> str:
        root = self.root(storage_id)
        candidate = Path(absolute_path).expanduser().resolve(strict=False)
        self._assert_within(root, candidate)
        relative = candidate.relative_to(root)
        return normalize_relative_path(relative.as_posix())

    def ensure_within(self, storage_id: str, path: str | os.PathLike[str]) -> Path:
        root = self.root(storage_id)
        candidate = Path(path).expanduser().resolve(strict=False)
        self._assert_within(root, candidate)
        return candidate

    @staticmethod
    def _assert_within(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise StorageEscapeError(f"path escapes storage root {root}: {candidate}") from exc


def load_storage_roots(
    config_path: str | os.PathLike[str] | None = None,
    *,
    overrides: Mapping[str, str | os.PathLike[str]] | None = None,
) -> StorageRoots:
    """Load fixed configuration, optionally replacing roots for tests/runtime."""

    if overrides is not None:
        return StorageRoots(overrides)
    path = Path(config_path).expanduser() if config_path is not None else _default_config_path()
    if path.is_file():
        return StorageRoots.from_json(path)
    if config_path is not None:
        raise FileNotFoundError(path)
    return StorageRoots(DEFAULT_STORAGE_ROOTS)


def coerce_storage_roots(
    value: StorageRoots | Mapping[str, str | os.PathLike[str]] | None,
) -> StorageRoots:
    if value is None:
        return load_storage_roots()
    if isinstance(value, StorageRoots):
        return value
    return StorageRoots(value)


def resolve_storage_path(
    storage_id: str,
    relative_path: str | os.PathLike[str],
    *,
    storage_roots: StorageRoots | Mapping[str, str | os.PathLike[str]] | None = None,
    require_exists: bool = False,
) -> Path:
    return coerce_storage_roots(storage_roots).resolve(
        storage_id, relative_path, require_exists=require_exists
    )
