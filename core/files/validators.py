"""Content validators for import sources and registerable files."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .readers import read_wav_metadata, tdms_metadata
from .scanner import FileKind, detect_file_kind, is_registered_kind


class FileValidationError(ValueError):
    """Raised when file content does not match its supported format."""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    kind: FileKind | None
    metadata: dict[str, object] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def raise_for_error(self) -> "ValidationResult":
        if not self.valid:
            raise FileValidationError("; ".join(self.errors) or "file validation failed")
        return self

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _failed(kind: FileKind | None, exc: BaseException | str) -> ValidationResult:
    message = str(exc)
    if isinstance(exc, BaseException):
        message = f"{type(exc).__name__}: {message}"
    return ValidationResult(False, kind, errors=(message,))


def validate_tdms(
    path: str | os.PathLike[str],
    *,
    compressed: bool | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> ValidationResult:
    source = Path(path)
    kind: FileKind = "tdms_zst" if compressed else "tdms"
    if compressed is None:
        detected = detect_file_kind(source)
        if detected not in {"tdms", "tdms_zst"}:
            return _failed(detected, f"not a TDMS import source: {source.name}")
        kind = detected
    try:
        metadata = tdms_metadata(
            source,
            compressed=(kind == "tdms_zst"),
            temp_dir=temp_dir,
        )
        groups = metadata.get("groups") or []
        channel_count = sum(
            len(group.get("channels") or [])
            for group in groups
            if isinstance(group, dict)
        )
        if channel_count <= 0:
            raise FileValidationError("TDMS contains no channels")
        metadata["group_count"] = len(groups)
        metadata["channel_count"] = channel_count
        return ValidationResult(True, kind, metadata=metadata)
    except Exception as exc:
        return _failed(kind, exc)


def validate_wav(path: str | os.PathLike[str]) -> ValidationResult:
    source = Path(path)
    kind = detect_file_kind(source)
    if kind != "wav":
        return _failed(kind, f"not a WAV import source: {source.name}")
    try:
        metadata = read_wav_metadata(source, verify_all_frames=True)
        if int(metadata.get("channels") or 0) <= 0:
            raise FileValidationError("WAV contains no channels")
        if int(metadata.get("sampling_rate_hz") or 0) <= 0:
            raise FileValidationError("WAV sampling rate is invalid")
        if int(metadata.get("frames") or 0) <= 0:
            raise FileValidationError("WAV contains no frames")
        return ValidationResult(True, "wav", metadata=metadata)
    except Exception as exc:
        return _failed("wav", exc)


def validate_import_source(
    path: str | os.PathLike[str],
    *,
    temp_dir: str | os.PathLike[str] | None = None,
) -> ValidationResult:
    kind = detect_file_kind(path)
    if kind in {"tdms", "tdms_zst"}:
        return validate_tdms(path, temp_dir=temp_dir)
    if kind == "wav":
        return validate_wav(path)
    return _failed(None, f"unsupported import-source suffix: {Path(path).name}")


def validate_registered_file(
    path: str | os.PathLike[str],
    *,
    temp_dir: str | os.PathLike[str] | None = None,
) -> ValidationResult:
    """Validate only formats permitted in the ``files`` table."""

    kind = detect_file_kind(path)
    if not is_registered_kind(kind):
        if kind == "tdms":
            return _failed(kind, "bare .tdms files must be compressed before registration")
        return _failed(kind, f"unsupported registered-file suffix: {Path(path).name}")
    return (
        validate_tdms(path, temp_dir=temp_dir)
        if kind == "tdms_zst"
        else validate_wav(path)
    )
