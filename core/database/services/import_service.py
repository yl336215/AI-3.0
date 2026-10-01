"""Transactional file import orchestration for AI-3.0 phase 1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any
from uuid import uuid4

from core.files import (
    DiscoveredSample,
    SampleDiscoveryResult,
    StorageRoots,
    compress_tdms,
    detect_file_kind,
    discover_samples,
    fingerprint_file,
    load_sample_profile,
    normalize_relative_path,
    validate_import_source,
)
from core.files.publisher import (
    acquire_publication_lock,
    publish_noreplace,
    release_publication_lock,
)
from core.files.scanner import is_excluded_directory, is_excluded_file

from ..repositories import (
    CONDITION_FIELDS,
    FileRepository,
    ImportRepository,
    SampleRepository,
)
from .database_service import DatabaseService


_RESERVED_EXTRA_FIELD_NAMES = frozenset(CONDITION_FIELDS) | {"extra_fields"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class ImportConflict(RuntimeError):
    """Raised when publishing would overwrite a different logical payload."""


class CleanupPending(RuntimeError):
    """Cleanup was refused while preserving the source at ``source_path``."""

    def __init__(self, message: str, source_path: Path):
        super().__init__(message)
        self.source_path = source_path


@dataclass(frozen=True)
class PreparedFile:
    source_path: Path
    source_kind: str
    target_path: Path
    target_relative_path: str
    fingerprint: Any
    discovery: Any
    published_now: bool


class ImportService:
    """Import one batch, committing each file independently.

    Physical publication intentionally precedes the database transaction.  If
    the transaction then fails, the item is retained as
    ``published_pending_registration`` so retrying can register the already
    published, verified file without overwriting it.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        storage_roots: StorageRoots | Mapping[str, str | os.PathLike[str]],
        profiles_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.database = DatabaseService(database_path)
        self.storage_roots = (
            storage_roots
            if isinstance(storage_roots, StorageRoots)
            else StorageRoots(storage_roots)
        )
        self.profiles_dir = Path(profiles_dir).expanduser() if profiles_dir else None

    @staticmethod
    def _normalize_conditions(values: Mapping[str, Any] | None) -> dict[str, Any]:
        conditions = dict(values or {})
        raw_extras = conditions.pop("extra_fields", {})
        unknown = set(conditions) - CONDITION_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported condition fields: {', '.join(sorted(unknown))}"
            )
        if not isinstance(raw_extras, Mapping):
            raise TypeError("extra_fields must be an object")
        if len(raw_extras) > 32:
            raise ValueError("extra_fields supports at most 32 fields")
        extra_fields: dict[str, Any] = {}
        for raw_key, value in raw_extras.items():
            if not isinstance(raw_key, str):
                raise TypeError("extra field names must be strings")
            key = raw_key.strip()
            if not key:
                raise ValueError("extra field names must not be empty")
            if len(key) > 80:
                raise ValueError("extra field names must not exceed 80 characters")
            if key in _RESERVED_EXTRA_FIELD_NAMES:
                raise ValueError(f"extra field name is reserved: {key}")
            if key in extra_fields:
                raise ValueError(f"duplicate extra field name after trimming: {key}")
            if value is not None and not isinstance(value, (str, bool, int, float)):
                raise TypeError(f"extra field values must be scalar or null: {key}")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"extra field numeric values must be finite: {key}")
            if value is not None:
                extra_fields[key] = value
        normalized: dict[str, Any] = {}
        for field in CONDITION_FIELDS:
            value = conditions.get(field)
            if isinstance(value, str):
                value = value.strip() or None
            if field in {"load_value", "speed_ratio"} and value is not None:
                if isinstance(value, bool):
                    raise TypeError(f"{field} must be numeric or null")
                value = float(value)
            normalized[field] = value
        normalized["extra_fields"] = extra_fields
        return normalized

    @staticmethod
    def _expand_source_paths(
        source_paths: Sequence[str | os.PathLike[str]],
    ) -> list[tuple[Path, str]]:
        """Expand selected folders deterministically without following symlinks.

        The second tuple item is the relative parent to retain for an external
        folder import.  Explicitly selected files keep the historical flat
        target behavior.  When a file and one of its parent folders are both
        selected, the folder selection wins so preview and confirmation do not
        depend on request order.
        """

        selected_directories: set[Path] = set()
        selected_files: set[Path] = set()
        for raw_path in source_paths:
            candidate = Path(os.path.abspath(Path(raw_path).expanduser()))
            # Do not resolve the final component before this check: doing so
            # would turn a selected symlink into an apparently ordinary file
            # or directory and could import content outside the chosen tree.
            if candidate.is_symlink():
                continue
            selected = candidate.resolve(strict=False)
            if selected.is_dir():
                if is_excluded_directory(selected.name):
                    continue
                selected_directories.add(selected)
                continue
            if (
                is_excluded_file(selected.name)
                or detect_file_kind(selected.name) is None
                or any(part.casefold() == "_ai3" for part in selected.parts)
                or any(part.casefold() == "databases" for part in selected.parts)
            ):
                continue

            # Preserve a supported missing path so the per-item preview can
            # report FileNotFoundError instead of silently dropping it.
            selected_files.add(selected)

        # Ignore a nested folder when an ancestor is already selected.  This
        # gives every discovered file one stable relative path.
        directory_roots: list[Path] = []
        for selected in sorted(
            selected_directories,
            key=lambda path: (len(path.parts), str(path).casefold(), str(path)),
        ):
            if any(
                selected == parent or selected.is_relative_to(parent)
                for parent in directory_roots
            ):
                continue
            directory_roots.append(selected)

        expanded: dict[Path, str] = {}
        for selected in directory_roots:
            for current, directory_names, file_names in os.walk(
                selected, followlinks=False
            ):
                current_path = Path(current)
                directory_names[:] = sorted(
                    (
                        name
                        for name in directory_names
                        if not is_excluded_directory(name)
                        and not (current_path / name).is_symlink()
                    ),
                    key=str.casefold,
                )
                for name in sorted(file_names, key=str.casefold):
                    path = current_path / name
                    if (
                        is_excluded_file(name)
                        or path.is_symlink()
                        or detect_file_kind(name) is None
                    ):
                        continue
                    relative = path.relative_to(selected)
                    parent_parts = (
                        ((selected.name,) if selected.name else ())
                        + relative.parts[:-1]
                    )
                    relative_parent = (
                        PurePosixPath(*parent_parts).as_posix()
                        if parent_parts
                        else ""
                    )
                    expanded[path.resolve(strict=False)] = relative_parent

        for selected in selected_files:
            if any(
                selected == directory or selected.is_relative_to(directory)
                for directory in directory_roots
            ):
                continue
            expanded[selected] = ""

        if not expanded:
            raise ValueError("selected paths contain no supported raw-data files")
        return sorted(
            expanded.items(),
            key=lambda item: (str(item[0]).casefold(), str(item[0]), item[1]),
        )

    def _profile(self, profile_id: str) -> dict[str, object]:
        name = profile_id.strip() or "default"
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("sample_profile must be a plain profile name")
        if self.profiles_dir is None:
            return load_sample_profile()
        candidates = (
            [self.profiles_dir / name]
            if Path(name).suffix.lower() in {".yaml", ".yml", ".json"}
            else [
                self.profiles_dir / f"{name}.yaml",
                self.profiles_dir / f"{name}.yml",
                self.profiles_dir / f"{name}.json",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return load_sample_profile(candidate)
        raise FileNotFoundError(f"sample profile not found: {name}")

    @staticmethod
    def _is_inside(root: Path, source: Path) -> bool:
        try:
            source.resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            return False

    def _target_for(
        self,
        source: Path,
        *,
        kind: str,
        source_scope: str,
        storage_id: str,
        target_relative_dir: str,
        source_relative_dir: str = "",
    ) -> tuple[Path, str]:
        if source_scope == "inside":
            if kind == "tdms":
                target = source.with_name(source.name + ".zst")
            else:
                target = source
            target = self.storage_roots.ensure_within(storage_id, target)
            relative = self.storage_roots.relative_path(storage_id, target)
        else:
            file_name = source.name + ".zst" if kind == "tdms" else source.name
            directory = normalize_relative_path(target_relative_dir, allow_empty=True)
            source_directory = normalize_relative_path(
                source_relative_dir, allow_empty=True
            )
            if source_directory:
                directory = (
                    PurePosixPath(directory, source_directory).as_posix()
                    if directory
                    else source_directory
                )
            relative = (
                PurePosixPath(directory, file_name).as_posix()
                if directory
                else PurePosixPath(file_name).as_posix()
            )
            target = self.storage_roots.resolve(storage_id, relative)
        if PurePosixPath(relative).parts[0].casefold() in {"_ai3", "databases"}:
            raise ValueError("raw files cannot be imported into a database management directory")
        return target, relative

    @staticmethod
    def _tdms_discovery_from_metadata(
        metadata: Mapping[str, object],
        *,
        profile: Mapping[str, object],
        manual_mapping: Sequence[Mapping[str, object]] | None,
    ) -> SampleDiscoveryResult:
        raw_mappings: object = (
            manual_mapping if manual_mapping is not None else profile.get("channels")
        )
        if not isinstance(raw_mappings, Sequence) or isinstance(
            raw_mappings, (str, bytes)
        ):
            raise ValueError("sample profile channels must be a list")
        group_index = {
            str(group.get("name")): group
            for group in metadata.get("groups", [])
            if isinstance(group, Mapping)
        }
        samples: list[DiscoveredSample] = []
        issues: list[str] = []
        seen_ids: set[str] = set()
        for sort_order, raw_mapping in enumerate(raw_mappings):
            if not isinstance(raw_mapping, Mapping):
                raise ValueError("each channel mapping must be an object")
            sample_id = str(raw_mapping.get("sample_id") or "").strip()
            group_name = str(raw_mapping.get("group_name") or "").strip()
            channel_name = str(raw_mapping.get("channel_name") or "").strip()
            if not sample_id or not group_name or not channel_name:
                raise ValueError(
                    "channel mapping requires sample_id, group_name and channel_name"
                )
            if sample_id in seen_ids:
                raise ValueError(f"duplicate sample_id in channel mapping: {sample_id}")
            seen_ids.add(sample_id)
            group = group_index.get(group_name)
            if group is None:
                issues.append(f"missing TDMS group: {group_name}")
                continue
            channel = next(
                (
                    candidate
                    for candidate in group.get("channels", [])
                    if isinstance(candidate, Mapping)
                    and str(candidate.get("name")) == channel_name
                ),
                None,
            )
            if channel is None:
                issues.append(f"missing TDMS channel: {group_name}/{channel_name}")
                continue
            samples.append(
                DiscoveredSample(
                    sample_id=sample_id,
                    sample_scope="channel",
                    display_name=str(raw_mapping.get("display_name") or sample_id),
                    locator_json={
                        "group_name": group_name,
                        "channel_name": channel_name,
                    },
                    sampling_rate_hz=(
                        float(channel["sampling_rate_hz"])
                        if channel.get("sampling_rate_hz") is not None
                        else None
                    ),
                    duration_s=(
                        float(channel["duration_s"])
                        if channel.get("duration_s") is not None
                        else None
                    ),
                    sort_order=sort_order,
                    metadata_json={"length": int(channel.get("length") or 0)},
                )
            )
        status = "ready" if samples and not issues else "pending_channel_mapping"
        return SampleDiscoveryResult(
            "tdms_zst",
            status,
            samples=tuple(samples),
            issues=tuple(issues),
            file_metadata=dict(metadata),
        )

    @staticmethod
    def _copy_and_publish(
        source: Path,
        target: Path,
        *,
        kind: str,
        storage_roots: StorageRoots,
        storage_id: str,
        profile: Mapping[str, object],
        manual_mapping: Sequence[Mapping[str, object]] | None,
        line: str | None,
        temp_dir: Path,
    ) -> tuple[Any, SampleDiscoveryResult]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target = storage_roots.ensure_within(storage_id, target)
        staging_dir = target.with_name(f".{target.name}.{uuid4().hex}.partial")
        storage_roots.ensure_within(storage_id, staging_dir)
        partial = staging_dir / target.name
        lock = acquire_publication_lock(target)
        try:
            staging_dir.mkdir(mode=0o700)
            if target.exists():
                raise FileExistsError(target)
            before = source.stat()
            source_fingerprint = fingerprint_file(source, kind=kind)
            with source.open("rb") as input_stream, partial.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=4 * 1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise RuntimeError("source changed while it was being copied")
            staged = fingerprint_file(partial, kind=kind)
            if (
                staged.payload_sha256 != source_fingerprint.payload_sha256
                or staged.stored_sha256 != source_fingerprint.stored_sha256
                or staged.payload_size_bytes != source_fingerprint.payload_size_bytes
                or staged.stored_size_bytes != source_fingerprint.stored_size_bytes
            ):
                raise IOError("full copied-file verification failed")
            validate_import_source(partial, temp_dir=temp_dir).raise_for_error()
            staged_discovery = discover_samples(
                partial,
                profile=profile,
                manual_mapping=manual_mapping,
                line=line,
                temp_dir=temp_dir,
            )
            if staged_discovery.status == "invalid" or (
                kind == "tdms_zst" and not staged_discovery.ready
            ):
                raise ValueError(
                    "; ".join(staged_discovery.issues)
                    or "staged sample discovery failed"
                )
            if target.exists():
                raise FileExistsError(target)
            publish_noreplace(partial, target)
            try:
                staging_dir.rmdir()
            except OSError:
                pass
            # Atomic rename preserves the inode and mtime represented by the
            # staged fingerprint; avoid a post-publication read that could
            # fail after the formal target is already visible.
            return staged, staged_discovery
        except BaseException:
            partial.unlink(missing_ok=True)
            try:
                staging_dir.rmdir()
            except OSError:
                pass
            raise
        finally:
            release_publication_lock(lock)

    def _prepare_file(
        self,
        source: Path,
        *,
        kind: str,
        source_scope: str,
        storage_id: str,
        target: Path,
        target_relative: str,
        profile: Mapping[str, object],
        manual_mapping: Sequence[Mapping[str, object]] | None,
        line: str | None,
    ) -> PreparedFile:
        temp_root = self.storage_roots.root(storage_id, require_available=True)
        source_before = source.stat()
        validation = validate_import_source(source, temp_dir=temp_root)
        validation.raise_for_error()
        discovery = discover_samples(
            source,
            profile=profile,
            manual_mapping=manual_mapping,
            line=line,
            temp_dir=temp_root,
        )
        if discovery.status == "invalid":
            raise ValueError("; ".join(discovery.issues) or "sample discovery failed")
        if kind in {"tdms", "tdms_zst"} and not discovery.ready:
            raise ValueError(
                "; ".join(discovery.issues)
                or "TDMS channel mapping is incomplete"
            )

        source_fingerprint = fingerprint_file(source, kind=kind)
        source_after = source.stat()
        if (
            source_before.st_dev,
            source_before.st_ino,
            source_before.st_size,
            source_before.st_mtime_ns,
            source_before.st_ctime_ns,
        ) != (
            source_after.st_dev,
            source_after.st_ino,
            source_after.st_size,
            source_after.st_mtime_ns,
            source_after.st_ctime_ns,
        ):
            raise RuntimeError("source changed during validation and sample discovery")
        if target.exists():
            target_kind = "tdms_zst" if kind == "tdms" else kind
            target_fingerprint = fingerprint_file(target, kind=target_kind)
            if target_fingerprint.payload_sha256 != source_fingerprint.payload_sha256:
                raise ImportConflict(
                    f"target exists with different content: {target_relative}"
                )
            target_discovery = discover_samples(
                target,
                profile=profile,
                manual_mapping=manual_mapping,
                line=line,
                temp_dir=temp_root,
            )
            if target_discovery.status == "invalid" or (
                target_kind == "tdms_zst" and not target_discovery.ready
            ):
                raise ValueError(
                    "; ".join(target_discovery.issues)
                    or "target sample discovery failed"
                )
            return PreparedFile(
                source,
                kind,
                target,
                target_relative,
                target_fingerprint,
                target_discovery,
                False,
            )

        if kind == "tdms":
            compressed = compress_tdms(
                source,
                target,
                storage_id=storage_id,
                storage_roots=self.storage_roots,
                expected_payload_sha256=source_fingerprint.payload_sha256,
            )
            fingerprint = compressed
        else:
            fingerprint, discovery = self._copy_and_publish(
                source,
                target,
                kind=kind,
                storage_roots=self.storage_roots,
                storage_id=storage_id,
                profile=profile,
                manual_mapping=manual_mapping,
                line=line,
                temp_dir=temp_root,
            )
        return PreparedFile(
            source,
            kind,
            target,
            target_relative,
            fingerprint,
            discovery,
            True,
        )

    def _remove_verified_internal_tdms(
        self,
        connection: Any,
        *,
        source: Path,
        prepared: PreparedFile,
        file_uid: str,
    ) -> None:
        """Remove only the same bare TDMS that was just registered."""

        self._remove_tdms_source_if_safe(
            connection,
            source=source,
            target=prepared.target_path,
            target_relative=prepared.target_relative_path,
            file_uid=file_uid,
            expected_payload=prepared.fingerprint.payload_sha256,
        )

    @staticmethod
    def _remove_tdms_source_if_safe(
        connection: Any,
        *,
        source: Path,
        target: Path,
        target_relative: str,
        file_uid: str,
        expected_payload: str,
    ) -> None:
        """Serialize cleanup with AI-3.0 publishers for the formal target."""

        try:
            lock = acquire_publication_lock(target)
        except Exception as exc:
            raise CleanupPending(
                f"cleanup could not lock the compressed target: {exc}", source
            ) from exc
        try:
            ImportService._remove_tdms_source_while_locked(
                connection,
                source=source,
                target=target,
                target_relative=target_relative,
                file_uid=file_uid,
                expected_payload=expected_payload,
            )
        finally:
            release_publication_lock(lock)

    @staticmethod
    def _remove_tdms_source_while_locked(
        connection: Any,
        *,
        source: Path,
        target: Path,
        target_relative: str,
        file_uid: str,
        expected_payload: str,
    ) -> None:
        """Quarantine, re-verify, then remove the exact registered source."""

        if not source.exists():
            return
        file_record = FileRepository(connection).get(file_uid)
        source_payload = fingerprint_file(source, kind="tdms").payload_sha256
        target_payload = fingerprint_file(target, kind="tdms_zst").payload_sha256
        if (
            file_record is None
            or file_record["record_status"] != "active"
            or file_record["relative_path"] != target_relative
            or source_payload != expected_payload
            or target_payload != expected_payload
            or file_record["payload_sha256"] != expected_payload
        ):
            raise CleanupPending(
                "cleanup refused because source, target, and database payloads differ",
                source,
            )

        quarantine_dir = source.with_name(
            f".{source.name}.{uuid4().hex}.cleanup.partial"
        )
        quarantine_dir.mkdir(mode=0o700)
        quarantined = quarantine_dir / source.name
        try:
            publish_noreplace(source, quarantined)
        except Exception as exc:
            try:
                quarantine_dir.rmdir()
            except OSError:
                pass
            raise CleanupPending(
                f"cleanup could not quarantine the source: {exc}", source
            ) from exc

        def restore_or_preserve(message: str, cause: Exception | None = None) -> None:
            recovery_path = quarantined
            if quarantined.exists() and not source.exists():
                try:
                    publish_noreplace(quarantined, source)
                    recovery_path = source
                    try:
                        quarantine_dir.rmdir()
                    except OSError:
                        pass
                except Exception:
                    pass
            error = CleanupPending(message, recovery_path)
            if cause is None:
                raise error
            raise error from cause

        try:
            quarantined_payload = fingerprint_file(
                quarantined, kind="tdms"
            ).payload_sha256
            target_payload = fingerprint_file(
                target, kind="tdms_zst"
            ).payload_sha256
            current_record = FileRepository(connection).get(file_uid)
            if (
                current_record is None
                or current_record["record_status"] != "active"
                or current_record["relative_path"] != target_relative
                or quarantined_payload != expected_payload
                or target_payload != expected_payload
                or current_record["payload_sha256"] != expected_payload
            ):
                restore_or_preserve(
                    "cleanup refused after quarantine because payloads changed"
                )
            quarantined.unlink()
        except CleanupPending:
            raise
        except Exception as exc:
            restore_or_preserve(f"cleanup could not remove quarantined source: {exc}", exc)
        try:
            quarantine_dir.rmdir()
        except OSError:
            pass

    def _register_prepared(
        self,
        connection: Any,
        prepared: PreparedFile,
        *,
        item_uuid: str,
        storage_id: str,
        conditions: Mapping[str, Any],
        planned_file_uid: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        files = FileRepository(connection)
        samples = SampleRepository(connection)
        imports = ImportRepository(connection)
        existing = files.get_by_path(storage_id, prepared.target_relative_path)
        if existing is not None:
            if existing["payload_sha256"] == prepared.fingerprint.payload_sha256:
                needs_refresh = (
                    prepared.published_now
                    or existing["availability_status"] != "present"
                    or existing["integrity_status"] != "verified"
                    or existing["stored_sha256"] != prepared.fingerprint.stored_sha256
                    or existing["stored_size_bytes"]
                    != prepared.fingerprint.stored_size_bytes
                )
                if needs_refresh:
                    refreshed = files.update(
                        existing["file_uid"],
                        {
                            "stored_sha256": prepared.fingerprint.stored_sha256,
                            "payload_size_bytes": prepared.fingerprint.payload_size_bytes,
                            "stored_size_bytes": prepared.fingerprint.stored_size_bytes,
                            "stored_mtime_ns": prepared.fingerprint.stored_mtime_ns,
                            "availability_status": "present",
                            "integrity_status": "verified",
                            "last_seen_at": _utc_now(),
                            "last_verified_at": _utc_now(),
                        },
                    )
                    for sample in samples.list_by_file(existing["file_uid"]):
                        samples.update(
                            existing["file_uid"],
                            sample["sample_id"],
                            {"availability_status": "present"},
                        )
                    imports.items.update(
                        item_uuid,
                        {
                            "status": "restored",
                            "payload_sha256": prepared.fingerprint.payload_sha256,
                            "stored_sha256": prepared.fingerprint.stored_sha256,
                        },
                    )
                    assert refreshed is not None
                    return "success", refreshed
                imports.items.update(
                    item_uuid,
                    {
                        "status": "duplicate",
                        "payload_sha256": prepared.fingerprint.payload_sha256,
                        "stored_sha256": prepared.fingerprint.stored_sha256,
                    },
                )
                return "skip", existing
            raise ImportConflict(
                f"active database path has different content: {prepared.target_relative_path}"
            )

        now = _utc_now()
        metadata = {
            "source_kind": prepared.source_kind,
            "sample_discovery_status": prepared.discovery.status,
            "sample_issues": list(prepared.discovery.issues),
            "validated_metadata": prepared.discovery.file_metadata,
        }
        condition_extras = dict(conditions.get("extra_fields") or {})
        if condition_extras:
            metadata["condition_extras"] = condition_extras
        fixed_conditions = {
            field: conditions.get(field) for field in CONDITION_FIELDS
        }
        file_record = files.create(
            {
                "file_uid": planned_file_uid or str(uuid4()),
                "storage_id": storage_id,
                "relative_path": prepared.target_relative_path,
                **fixed_conditions,
                "payload_sha256": prepared.fingerprint.payload_sha256,
                "stored_sha256": prepared.fingerprint.stored_sha256,
                "payload_size_bytes": prepared.fingerprint.payload_size_bytes,
                "stored_size_bytes": prepared.fingerprint.stored_size_bytes,
                "stored_mtime_ns": prepared.fingerprint.stored_mtime_ns,
                "last_seen_at": now,
                "last_verified_at": now,
                "metadata_json": metadata,
            }
        )
        for sample in prepared.discovery.samples:
            samples.create({"file_uid": file_record["file_uid"], **sample.to_dict()})
        status = (
            "registered"
            if prepared.discovery.status == "ready"
            else "registered_pending_channel_selection"
        )
        imports.items.update(
            item_uuid,
            {
                "status": status,
                "payload_sha256": prepared.fingerprint.payload_sha256,
                "stored_sha256": prepared.fingerprint.stored_sha256,
                "error_message": (
                    None
                    if prepared.discovery.ready
                    else "; ".join(prepared.discovery.issues)
                ),
            },
        )
        return "success", file_record

    def preview_paths(
        self,
        source_paths: Sequence[str | os.PathLike[str]],
        *,
        target_storage_id: str = "wuxi_raw",
        target_relative_dir: str = "",
        expected_source_scope: str | None = None,
        transfer_mode: str = "copy",
        expected_file_kind: str | None = None,
        conditions: Mapping[str, Any] | None = None,
        sample_profile: str = "default",
        channel_mappings: Sequence[Mapping[str, object]] | None = None,
    ) -> dict[str, Any]:
        """Validate a batch and show files/samples without writing anything."""

        if not source_paths:
            raise ValueError("source_paths must not be empty")
        if transfer_mode not in {"copy", "move"}:
            raise ValueError("transfer_mode must be copy or move")
        root = self.storage_roots.root(target_storage_id, require_available=True)
        sources = self._expand_source_paths(source_paths)
        if expected_file_kind is not None:
            sources = [
                (path, relative_dir)
                for path, relative_dir in sources
                if (
                    detect_file_kind(path) in {"tdms", "tdms_zst"}
                    if expected_file_kind == "tdms"
                    else detect_file_kind(path) == expected_file_kind
                )
            ]
            if not sources:
                raise ValueError("所选文件或文件夹中没有指定类型的文件")
        scopes = {
            "inside" if self._is_inside(root, path) else "outside"
            for path, _ in sources
        }
        if len(scopes) != 1:
            raise ValueError("inside and outside files must be imported in separate batches")
        source_scope = scopes.pop()
        if expected_source_scope is not None and source_scope != expected_source_scope:
            raise ValueError(
                "导入方式与文件位置不匹配，请重新选择“根目录内登记”"
                "或“根目录外导入”"
            )
        normalized_conditions = self._normalize_conditions(conditions)
        profile = self._profile(sample_profile)
        items: list[dict[str, Any]] = []

        connection = self.database.connect(readonly=True)
        try:
            files = FileRepository(connection)
            for source, source_relative_dir in sources:
                item: dict[str, Any] = {
                    "source_display_path": str(source),
                    "source_scope": source_scope,
                    "status": "failed",
                    "issues": [],
                    "samples": [],
                }
                try:
                    if not source.is_file():
                        raise FileNotFoundError(source)
                    kind = detect_file_kind(source)
                    if kind is None:
                        raise ValueError(
                            f"unsupported import-source suffix: {source.name}"
                        )
                    target, target_relative = self._target_for(
                        source,
                        kind=kind,
                        source_scope=source_scope,
                        storage_id=target_storage_id,
                        target_relative_dir=target_relative_dir,
                        source_relative_dir=source_relative_dir,
                    )
                    item.update(
                        {
                            "file_uid": str(uuid4()),
                            "kind": kind,
                            "action": "compress"
                            if kind == "tdms"
                            else (
                                "register"
                                if source_scope == "inside"
                                else transfer_mode
                            ),
                            "target_relative_path": target_relative,
                        }
                    )
                    validation = validate_import_source(
                        source, temp_dir=root
                    ).raise_for_error()
                    discovery = discover_samples(
                        source,
                        profile=profile,
                        manual_mapping=channel_mappings,
                        line=str(normalized_conditions.get("line") or "") or None,
                        temp_dir=root,
                    )
                    if discovery.status == "invalid" or (
                        kind in {"tdms", "tdms_zst"} and not discovery.ready
                    ):
                        raise ValueError(
                            "; ".join(discovery.issues)
                            or "sample discovery failed"
                        )
                    source_fingerprint = fingerprint_file(source, kind=kind)
                    item.update(
                        {
                            "validation": validation.to_dict(),
                            "sample_discovery_status": discovery.status,
                            "issues": list(discovery.issues),
                            "samples": [sample.to_dict() for sample in discovery.samples],
                            "payload_sha256": source_fingerprint.payload_sha256,
                            "payload_size_bytes": source_fingerprint.payload_size_bytes,
                        }
                    )
                    if target.exists():
                        target_kind = "tdms_zst" if kind == "tdms" else kind
                        target_fingerprint = fingerprint_file(target, kind=target_kind)
                        if (
                            target_fingerprint.payload_sha256
                            != source_fingerprint.payload_sha256
                        ):
                            item["status"] = "conflict"
                            item["issues"] = [
                                f"target exists with different content: {target_relative}"
                            ]
                        else:
                            existing = files.get_by_path(
                                target_storage_id, target_relative
                            )
                            if existing is None:
                                item["status"] = "ready"
                            elif (
                                existing["payload_sha256"]
                                == source_fingerprint.payload_sha256
                            ):
                                item["status"] = (
                                    "ready"
                                    if existing["availability_status"] != "present"
                                    or existing["integrity_status"] != "verified"
                                    or existing["stored_sha256"]
                                    != target_fingerprint.stored_sha256
                                    else "duplicate"
                                )
                            else:
                                item["status"] = "conflict"
                                item["issues"] = [
                                    "active database path refers to different logical content"
                                ]
                    else:
                        existing = files.get_by_path(
                            target_storage_id, target_relative
                        )
                        if existing is not None and (
                            existing["payload_sha256"]
                            != source_fingerprint.payload_sha256
                        ):
                            item["status"] = "conflict"
                            item["issues"] = [
                                "active database path refers to different logical content"
                            ]
                        else:
                            item["status"] = "ready"
                except Exception as exc:
                    item["issues"] = [f"{type(exc).__name__}: {exc}"]
                items.append(item)
        finally:
            connection.close()

        # A target path is the database-level identity of an imported file.
        # Detect collisions across the whole request only after every item has
        # been resolved so the result is independent of source-path order.
        items_by_target: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            target_relative = item.get("target_relative_path")
            if isinstance(target_relative, str):
                items_by_target.setdefault(target_relative, []).append(item)
        for target_relative, target_items in items_by_target.items():
            if len(target_items) < 2:
                continue
            issue = f"multiple sources map to the same target: {target_relative}"
            for item in target_items:
                item["status"] = "conflict"
                existing_issues = list(item.get("issues") or [])
                if issue not in existing_issues:
                    existing_issues.append(issue)
                item["issues"] = existing_issues

        counts = {
            "total_count": len(items),
            "ready_count": sum(item["status"] == "ready" for item in items),
            "duplicate_count": sum(item["status"] == "duplicate" for item in items),
            "conflict_count": sum(item["status"] == "conflict" for item in items),
            "failed_count": sum(item["status"] == "failed" for item in items),
        }
        return {
            **counts,
            # UI compatibility: preview-ready is the number that can succeed on
            # confirmation; no data has been written at this point.
            "success_count": counts["ready_count"],
            "skip_count": counts["duplicate_count"],
            "source_scope": source_scope,
            "target_storage_id": target_storage_id,
            "target_relative_dir": normalize_relative_path(
                target_relative_dir, allow_empty=True
            ),
            "conditions": normalized_conditions,
            "items": items,
            "mutated": False,
        }

    def import_paths(
        self,
        source_paths: Sequence[str | os.PathLike[str]],
        *,
        target_storage_id: str = "wuxi_raw",
        target_relative_dir: str = "",
        expected_source_scope: str | None = None,
        transfer_mode: str = "copy",
        expected_file_kind: str | None = None,
        conditions: Mapping[str, Any] | None = None,
        sample_profile: str = "default",
        channel_mappings: Sequence[Mapping[str, object]] | None = None,
        planned_file_uids: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not source_paths:
            raise ValueError("source_paths must not be empty")
        if transfer_mode not in {"copy", "move"}:
            raise ValueError("transfer_mode must be copy or move")
        root = self.storage_roots.root(target_storage_id, require_available=True)
        sources = self._expand_source_paths(source_paths)
        if expected_file_kind is not None:
            sources = [
                (path, relative_dir)
                for path, relative_dir in sources
                if (
                    detect_file_kind(path) in {"tdms", "tdms_zst"}
                    if expected_file_kind == "tdms"
                    else detect_file_kind(path) == expected_file_kind
                )
            ]
            if not sources:
                raise ValueError("所选文件或文件夹中没有指定类型的文件")
        scopes = {
            "inside" if self._is_inside(root, path) else "outside"
            for path, _ in sources
        }
        if len(scopes) != 1:
            raise ValueError("inside and outside files must be imported in separate batches")
        source_scope = scopes.pop()
        if expected_source_scope is not None and source_scope != expected_source_scope:
            raise ValueError(
                "导入方式与文件位置不匹配，请重新选择“根目录内登记”"
                "或“根目录外导入”"
            )
        normalized_conditions = self._normalize_conditions(conditions)
        profile = self._profile(sample_profile)

        connection = self.database.connect()
        imports = ImportRepository(connection)
        operation = "register" if source_scope == "inside" else "import"
        try:
            with self.database.transaction(connection):
                run = imports.runs.create(
                    {
                        "operation": operation,
                        "source_scope": source_scope,
                        "target_storage_id": target_storage_id,
                        "conditions_json": normalized_conditions,
                        "status": "running",
                        "total_count": len(sources),
                    }
                )

            counters = {"success": 0, "skip": 0, "conflict": 0, "failed": 0}
            attention = False
            imported_files: list[dict[str, Any]] = []
            for source, source_relative_dir in sources:
                kind = detect_file_kind(source)
                action = "compress" if kind == "tdms" else (
                    "register" if source_scope == "inside" else "copy"
                )
                target: Path | None = None
                target_relative: str | None = None
                if kind is not None:
                    try:
                        target, target_relative = self._target_for(
                            source,
                            kind=kind,
                            source_scope=source_scope,
                            storage_id=target_storage_id,
                            target_relative_dir=target_relative_dir,
                            source_relative_dir=source_relative_dir,
                        )
                    except Exception:
                        # Persist a per-item error below rather than aborting the batch.
                        pass
                with self.database.transaction(connection):
                    item = imports.items.create(
                        {
                            "run_uuid": run["run_uuid"],
                            "source_display_path": str(source),
                            "target_relative_path": target_relative,
                            "action": action,
                            "status": "planned",
                        }
                    )

                prepared: PreparedFile | None = None
                try:
                    if not source.is_file():
                        raise FileNotFoundError(source)
                    if kind is None:
                        raise ValueError(f"unsupported import-source suffix: {source.name}")
                    if target is None or target_relative is None:
                        target, target_relative = self._target_for(
                            source,
                            kind=kind,
                            source_scope=source_scope,
                            storage_id=target_storage_id,
                            target_relative_dir=target_relative_dir,
                            source_relative_dir=source_relative_dir,
                        )
                    with self.database.transaction(connection):
                        imports.items.update(
                            item["item_uuid"],
                            {
                                "target_relative_path": target_relative,
                                "status": "writing" if target != source else "verified",
                            },
                        )
                    with self.database.transaction(connection):
                        # Hold the database write reservation across the final
                        # path check, physical publication, and registration.
                        # This prevents another AI-3.0 process from changing
                        # the active path contract between those steps.
                        existing = FileRepository(connection).get_by_path(
                            target_storage_id, target_relative
                        )
                        if existing is not None:
                            source_payload = fingerprint_file(
                                source, kind=kind
                            ).payload_sha256
                            if existing["payload_sha256"] != source_payload:
                                raise ImportConflict(
                                    "active database path refers to different logical content: "
                                    f"{target_relative}"
                                )
                        prepared = self._prepare_file(
                            source,
                            kind=kind,
                            source_scope=source_scope,
                            storage_id=target_storage_id,
                            target=target,
                            target_relative=target_relative,
                            profile=profile,
                            manual_mapping=channel_mappings,
                            line=str(normalized_conditions.get("line") or "") or None,
                        )
                        outcome, file_record = self._register_prepared(
                            connection,
                            prepared,
                            item_uuid=item["item_uuid"],
                            storage_id=target_storage_id,
                            conditions=normalized_conditions,
                            planned_file_uid=(planned_file_uids or {}).get(target_relative),
                        )
                    counters[outcome] += 1
                    if outcome == "success":
                        imported_files.append(file_record)
                        if not prepared.discovery.ready:
                            attention = True

                        if source_scope == "outside" and transfer_mode == "move":
                            try:
                                source.unlink()
                            except OSError as exc:
                                attention = True
                                with self.database.transaction(connection):
                                    imports.items.update(
                                        item["item_uuid"],
                                        {"error_message": f"目标已登记，但源文件未能删除: {exc}"},
                                    )

                    if source_scope == "inside" and kind == "tdms":
                        try:
                            self._remove_verified_internal_tdms(
                                connection,
                                source=source,
                                prepared=prepared,
                                file_uid=str(file_record["file_uid"]),
                            )
                        except Exception as exc:
                            attention = True
                            cleanup_source = (
                                str(exc.source_path)
                                if isinstance(exc, CleanupPending)
                                else str(source)
                            )
                            with self.database.transaction(connection):
                                imports.items.update(
                                    item["item_uuid"],
                                    {
                                        "status": "cleanup_pending",
                                        "source_display_path": cleanup_source,
                                        "error_message": str(exc),
                                    },
                                )
                except ImportConflict as exc:
                    counters["conflict"] += 1
                    with self.database.transaction(connection):
                        imports.items.update(
                            item["item_uuid"],
                            {"status": "conflict", "error_message": str(exc)},
                        )
                except Exception as exc:
                    counters["failed"] += 1
                    status = (
                        "published_pending_registration"
                        if prepared is not None and prepared.published_now
                        else "failed"
                    )
                    with self.database.transaction(connection):
                        imports.items.update(
                            item["item_uuid"],
                            {"status": status, "error_message": f"{type(exc).__name__}: {exc}"},
                        )

            if counters["failed"] or counters["conflict"]:
                final_status = "completed_with_issues"
            elif attention:
                final_status = "completed_with_attention"
            else:
                final_status = "completed"
            with self.database.transaction(connection):
                final_run = imports.runs.update(
                    run["run_uuid"],
                    {
                        "status": final_status,
                        "success_count": counters["success"],
                        "skip_count": counters["skip"],
                        "conflict_count": counters["conflict"],
                        "failed_count": counters["failed"],
                        "finished_at": _utc_now(),
                    },
                )
            return {
                "run": final_run,
                "items": imports.items.list_by_run(run["run_uuid"]),
                "files": imported_files,
            }
        finally:
            connection.close()

    def retry_cleanup(self, item_uuid: str) -> dict[str, Any]:
        """Safely retry deletion of an internal bare TDMS after DB commit."""

        connection = self.database.connect()
        imports = ImportRepository(connection)
        cleanup_item = False
        try:
            item = imports.items.get(item_uuid)
            if item is None:
                raise KeyError(f"import item does not exist: {item_uuid}")
            if item["status"] != "cleanup_pending":
                raise ValueError("import item is not waiting for source cleanup")
            cleanup_item = True
            run = imports.runs.get(item["run_uuid"])
            if run is None or run["source_scope"] != "inside":
                raise ValueError("cleanup is only valid for an inside import")
            storage_id = str(run["target_storage_id"])
            source = Path(str(item["source_display_path"])).resolve(strict=False)
            self.storage_roots.ensure_within(storage_id, source)
            if detect_file_kind(source) != "tdms":
                raise ValueError("cleanup source must be a bare .tdms file")
            if not source.exists():
                with self.database.transaction(connection):
                    updated = imports.items.update(
                        item_uuid, {"status": "registered", "error_message": None}
                    )
                assert updated is not None
                return updated
            target_relative = str(item["target_relative_path"] or "")
            target = self.storage_roots.resolve(
                storage_id, target_relative, require_exists=True
            )
            if detect_file_kind(target) != "tdms_zst":
                raise ValueError("cleanup target must be a .tdms.zst file")
            file_record = FileRepository(connection).get_by_path(
                storage_id, target_relative
            )
            if file_record is None:
                raise ImportConflict("cleanup refused because the database row is missing")
            self._remove_tdms_source_if_safe(
                connection,
                source=source,
                target=target,
                target_relative=target_relative,
                file_uid=str(file_record["file_uid"]),
                expected_payload=str(file_record["payload_sha256"]),
            )
            with self.database.transaction(connection):
                updated = imports.items.update(
                    item_uuid, {"status": "registered", "error_message": None}
                )
            assert updated is not None
            return updated
        except Exception as exc:
            if cleanup_item:
                cleanup_source = (
                    str(exc.source_path)
                    if isinstance(exc, CleanupPending)
                    else str(item["source_display_path"])
                )
                with self.database.transaction(connection):
                    imports.items.update(
                        item_uuid,
                        {
                            "status": "cleanup_pending",
                            "source_display_path": cleanup_source,
                            "error_message": f"{type(exc).__name__}: {exc}",
                        },
                    )
            raise
        finally:
            connection.close()


__all__ = ["ImportConflict", "ImportService"]
