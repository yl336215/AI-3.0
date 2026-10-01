"""Storage scanner with explicit import-source classification."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .storage import (
    DEFAULT_STORAGE_ID,
    StorageRoots,
    StorageUnavailableError,
    coerce_storage_roots,
    normalize_relative_path,
)


FileKind = Literal["tdms", "tdms_zst", "wav"]
IMPORT_SOURCE_SUFFIXES = (".tdms.zst", ".tdms", ".wav")
REGISTERED_SUFFIXES = (".tdms.zst", ".wav")

_SUFFIX_KIND: tuple[tuple[str, FileKind], ...] = tuple(
    sorted(
        ((".tdms.zst", "tdms_zst"), (".tdms", "tdms"), (".wav", "wav")),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_EXCLUDED_DIR_NAMES = {
    "_ai3",
    ".tmp",
    "_tmp",
    "tmp",
    ".temp",
    "_temp",
    "temp",
    "temporary",
    "databases",
    "backups",
    "exports",
    "logs",
}
_EXCLUDED_DIR_SUFFIXES = (".partial", ".tmp", ".temp")
_EXCLUDED_FILE_SUFFIXES = (
    ".partial",
    ".ai3-publish.lock",
    "-wal",
    "-shm",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".bak",
    ".backup",
)


def detect_file_kind(path_or_name: str | os.PathLike[str]) -> FileKind | None:
    """Classify by longest complete filename suffix, ignoring case."""

    name = Path(os.fspath(path_or_name)).name.lower()
    for suffix, kind in _SUFFIX_KIND:
        if name.endswith(suffix):
            return kind
    return None


def is_registered_kind(kind: FileKind | None) -> bool:
    return kind in {"tdms_zst", "wav"}


def is_excluded_directory(name: str) -> bool:
    lower = name.lower()
    return lower in _EXCLUDED_DIR_NAMES or lower.endswith(_EXCLUDED_DIR_SUFFIXES)


def is_excluded_file(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(_EXCLUDED_FILE_SUFFIXES)


@dataclass(frozen=True)
class ScanEntry:
    storage_id: str
    relative_path: str
    absolute_path: str
    kind: FileKind
    size_bytes: int
    mtime_ns: int
    action: Literal["register", "compress"]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ScanIssue:
    relative_path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class ScanResult:
    storage_id: str
    scope: str
    entries: list[ScanEntry] = field(default_factory=list)
    unrecognized: list[ScanIssue] = field(default_factory=list)
    excluded_count: int = 0

    @property
    def registerable(self) -> list[ScanEntry]:
        return [entry for entry in self.entries if entry.action == "register"]

    @property
    def needs_compression(self) -> list[ScanEntry]:
        return [entry for entry in self.entries if entry.action == "compress"]

    def to_dict(self) -> dict[str, object]:
        return {
            "storage_id": self.storage_id,
            "scope": self.scope,
            "entries": [entry.to_dict() for entry in self.entries],
            "unrecognized": [issue.to_dict() for issue in self.unrecognized],
            "excluded_count": self.excluded_count,
        }


def scan_storage(
    storage_id: str = DEFAULT_STORAGE_ID,
    *,
    relative_dir: str = "",
    storage_roots: StorageRoots | dict[str, str | os.PathLike[str]] | None = None,
    include_unrecognized: bool = True,
) -> ScanResult:
    """Scan one accessible storage scope without following directory symlinks."""

    roots = coerce_storage_roots(storage_roots)
    root = roots.root(storage_id, require_available=True)
    if relative_dir:
        scope_relative = normalize_relative_path(relative_dir)
        scope_path = roots.resolve(storage_id, scope_relative, require_exists=True)
    else:
        scope_relative = ""
        scope_path = root
    if not scope_path.is_dir():
        raise StorageUnavailableError(f"scan scope is not a directory: {scope_path}")

    result = ScanResult(storage_id=storage_id, scope=scope_relative)
    if scope_relative and any(
        is_excluded_directory(part) for part in Path(scope_relative).parts
    ):
        result.excluded_count = 1
        return result
    for current, directory_names, file_names in os.walk(scope_path, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in directory_names:
            directory = current_path / name
            if is_excluded_directory(name) or directory.is_symlink():
                result.excluded_count += 1
            else:
                kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in file_names:
            path = current_path / name
            if is_excluded_file(name) or path.is_symlink():
                result.excluded_count += 1
                continue
            try:
                relative = roots.relative_path(storage_id, path)
            except ValueError:
                result.excluded_count += 1
                continue
            kind = detect_file_kind(name)
            if kind is None:
                if include_unrecognized:
                    result.unrecognized.append(ScanIssue(relative, "unrecognized_suffix"))
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                result.unrecognized.append(ScanIssue(relative, f"stat_failed: {exc}"))
                continue
            result.entries.append(
                ScanEntry(
                    storage_id=storage_id,
                    relative_path=relative,
                    absolute_path=str(path.resolve(strict=False)),
                    kind=kind,
                    size_bytes=int(stat.st_size),
                    mtime_ns=int(stat.st_mtime_ns),
                    action="compress" if kind == "tdms" else "register",
                )
            )
    result.entries.sort(key=lambda item: item.relative_path.casefold())
    result.unrecognized.sort(key=lambda item: item.relative_path.casefold())
    return result
