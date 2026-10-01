"""Streaming stored-file and logical-payload SHA-256 fingerprints."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

from .readers.tdms import iter_decompressed_chunks
from .scanner import FileKind, detect_file_kind


HASH_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class Digest:
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FileFingerprint:
    payload_sha256: str
    stored_sha256: str
    payload_size_bytes: int
    stored_size_bytes: int
    stored_mtime_ns: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _digest_chunks(chunks: Iterable[bytes]) -> Digest:
    hasher = hashlib.sha256()
    size = 0
    for chunk in chunks:
        if not chunk:
            continue
        hasher.update(chunk)
        size += len(chunk)
    return Digest(hasher.hexdigest(), size)


def _file_chunks(stream: BinaryIO, chunk_size: int = HASH_CHUNK_SIZE) -> Iterable[bytes]:
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        yield chunk


def stored_fingerprint(path: str | os.PathLike[str]) -> Digest:
    source = Path(path)
    with source.open("rb") as stream:
        return _digest_chunks(_file_chunks(stream))


def payload_fingerprint(
    path: str | os.PathLike[str], *, kind: FileKind | None = None
) -> Digest:
    source = Path(path)
    selected_kind = kind or detect_file_kind(source)
    if selected_kind is None:
        raise ValueError(f"unsupported file suffix: {source.name}")
    if selected_kind == "tdms_zst":
        return _digest_chunks(iter_decompressed_chunks(source))
    # An uncompressed TDMS payload and a WAV payload are their stored bytes.
    return stored_fingerprint(source)


def fingerprint_file(
    path: str | os.PathLike[str], *, kind: FileKind | None = None
) -> FileFingerprint:
    source = Path(path)
    selected_kind = kind or detect_file_kind(source)
    if selected_kind is None:
        raise ValueError(f"unsupported file suffix: {source.name}")
    before = source.stat()
    stored = stored_fingerprint(source)
    middle = source.stat()
    if _stat_identity(before) != _stat_identity(middle):
        raise RuntimeError(f"file changed while it was being fingerprinted: {source}")
    if selected_kind == "tdms_zst":
        payload = payload_fingerprint(source, kind=selected_kind)
        after = source.stat()
        if _stat_identity(middle) != _stat_identity(after):
            raise RuntimeError(f"file changed while it was being fingerprinted: {source}")
    else:
        # Bare TDMS and WAV logical payloads are exactly their stored bytes.
        payload = stored
        after = middle
    return FileFingerprint(
        payload_sha256=payload.sha256,
        stored_sha256=stored.sha256,
        payload_size_bytes=payload.size_bytes,
        stored_size_bytes=stored.size_bytes,
        stored_mtime_ns=int(after.st_mtime_ns),
    )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def sha256_file(path: str | os.PathLike[str]) -> str:
    return stored_fingerprint(path).sha256


def payload_sha256(path: str | os.PathLike[str], *, kind: FileKind | None = None) -> str:
    return payload_fingerprint(path, kind=kind).sha256
