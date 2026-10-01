"""Safe streaming TDMS -> TDMS.ZST publication."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import zstandard as zstd

from .fingerprint import payload_fingerprint, stored_fingerprint
from .publisher import (
    acquire_publication_lock,
    publish_noreplace,
    release_publication_lock,
)
from .storage import (
    DEFAULT_STORAGE_ID,
    StorageRoots,
    coerce_storage_roots,
)
from .validators import validate_tdms


COPY_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class CompressionResult:
    source_path: str
    target_path: str
    storage_id: str
    target_relative_path: str
    payload_sha256: str
    stored_sha256: str
    payload_size_bytes: int
    stored_size_bytes: int
    stored_mtime_ns: int
    tdms_metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _resolve_target(
    target_path: str | os.PathLike[str],
    *,
    storage_id: str,
    storage_roots: StorageRoots,
) -> tuple[Path, str]:
    candidate = Path(target_path).expanduser()
    if candidate.is_absolute():
        target = storage_roots.ensure_within(storage_id, candidate)
        relative = storage_roots.relative_path(storage_id, target)
    else:
        target = storage_roots.resolve(storage_id, os.fspath(target_path))
        relative = storage_roots.relative_path(storage_id, target)
    if not target.name.lower().endswith(".tdms.zst"):
        raise ValueError(f"compressed TDMS target must end with .tdms.zst: {target.name}")
    return target, relative


def compress_tdms(
    source_path: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
    *,
    storage_id: str = DEFAULT_STORAGE_ID,
    storage_roots: StorageRoots | Mapping[str, str | os.PathLike[str]] | None = None,
    compression_level: int = 10,
    threads: int = 0,
    overwrite: bool = False,
    expected_payload_sha256: str | None = None,
) -> CompressionResult:
    """Compress and atomically publish a TDMS while preserving the source.

    Source cleanup is deliberately *not* part of this function.  For an
    in-storage import, the import service may delete the bare source only after
    its database transaction commits.
    """

    if overwrite:
        raise ValueError("AI-3.0 never overwrites a formal raw-data target")

    source = Path(source_path).expanduser().resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(source)
    if not source.name.lower().endswith(".tdms") or source.name.lower().endswith(".tdms.zst"):
        raise ValueError(f"source must be a bare .tdms file: {source.name}")

    roots = coerce_storage_roots(storage_roots)
    roots.root(storage_id, require_available=True)
    target, target_relative = _resolve_target(
        target_path, storage_id=storage_id, storage_roots=roots
    )
    if source == target:
        raise ValueError("source and compressed target must differ")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Resolve again after mkdir so a concurrently introduced symlink cannot
    # redirect publication outside the storage root.
    target = roots.ensure_within(storage_id, target)
    partial = target.with_name(f"{target.name}.{uuid4().hex}.partial")
    roots.ensure_within(storage_id, partial)

    lock = acquire_publication_lock(target)
    payload_hasher = hashlib.sha256()
    payload_size = 0
    partial_created = False
    try:
        before = source.stat()
        if target.exists():
            raise FileExistsError(target)
        compressor = zstd.ZstdCompressor(
            level=int(compression_level),
            threads=max(0, int(threads)),
            write_checksum=True,
        )
        with source.open("rb") as input_stream, partial.open("xb") as output_stream:
            partial_created = True
            with compressor.stream_writer(output_stream, closefd=False) as writer:
                while True:
                    chunk = input_stream.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    payload_hasher.update(chunk)
                    payload_size += len(chunk)
                    writer.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())

        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("source TDMS changed while it was being compressed")

        restored = payload_fingerprint(partial, kind="tdms_zst")
        expected_hash = payload_hasher.hexdigest()
        if (
            expected_payload_sha256 is not None
            and expected_hash != expected_payload_sha256
        ):
            raise RuntimeError(
                "source TDMS no longer matches the validated import preview"
            )
        if restored.sha256 != expected_hash or restored.size_bytes != payload_size:
            raise IOError("full TDMS decompression verification failed")

        validation = validate_tdms(partial, compressed=True).raise_for_error()
        stored = stored_fingerprint(partial)
        stored_mtime_ns = int(partial.stat().st_mtime_ns)
        if target.exists():
            raise FileExistsError(target)
        publish_noreplace(partial, target)
        partial_created = False
        return CompressionResult(
            source_path=str(source),
            target_path=str(target),
            storage_id=storage_id,
            target_relative_path=target_relative,
            payload_sha256=expected_hash,
            stored_sha256=stored.sha256,
            payload_size_bytes=payload_size,
            stored_size_bytes=stored.size_bytes,
            stored_mtime_ns=stored_mtime_ns,
            tdms_metadata=validation.metadata,
        )
    except Exception:
        if partial_created:
            partial.unlink(missing_ok=True)
        raise
    finally:
        release_publication_lock(lock)


def compress_tdms_to_storage(
    source_path: str | os.PathLike[str],
    target_relative_path: str,
    **kwargs: object,
) -> CompressionResult:
    return compress_tdms(source_path, target_relative_path, **kwargs)
