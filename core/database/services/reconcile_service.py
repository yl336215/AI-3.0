"""Preview-first storage reconciliation for AI-3.0."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.files import (
    ScanEntry,
    ScanIssue,
    ScanResult,
    StorageRoots,
    detect_file_kind,
    discover_samples,
    fingerprint_file,
    load_sample_profile,
    normalize_relative_path,
    scan_storage,
)
from core.files.scanner import is_excluded_directory, is_excluded_file

from ..repositories import CONDITION_FIELDS, FileRepository, SampleRepository
from .database_service import DatabaseService
from .import_service import ImportService


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class ReconcileConflict(RuntimeError):
    """Raised when a preview is stale or an action is no longer safe."""


class ReconcileService:
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
    def _item(category: str, **values: Any) -> dict[str, Any]:
        item = {"item_id": str(uuid4()), "category": category, **values}
        return item

    @staticmethod
    def _require_preview_record(
        files: FileRepository,
        item: Mapping[str, Any],
        *,
        storage_id: str,
    ) -> dict[str, Any]:
        record = files.get(str(item["file_uid"]))
        if (
            record is None
            or record["record_status"] != "active"
            or record["storage_id"] != storage_id
            or record["relative_path"] != item.get("old_relative_path")
        ):
            raise ReconcileConflict(
                "database record changed after preview; rescan before applying"
            )
        return record

    def _scan_file(
        self,
        *,
        storage_id: str,
        relative_path: str,
    ) -> ScanResult:
        """Inspect exactly one path without walking its parent directory."""

        result = ScanResult(storage_id=storage_id, scope=relative_path)
        parts = Path(relative_path).parts
        if any(is_excluded_directory(part) for part in parts[:-1]) or is_excluded_file(
            parts[-1]
        ):
            result.excluded_count = 1
            return result

        path = self.storage_roots.resolve(storage_id, relative_path)
        if not path.exists():
            return result
        if not path.is_file():
            result.unrecognized.append(ScanIssue(relative_path, "not_a_file"))
            return result

        kind = detect_file_kind(path.name)
        if kind is None:
            result.unrecognized.append(
                ScanIssue(relative_path, "unrecognized_suffix")
            )
            return result
        try:
            stat = path.stat()
        except OSError as exc:
            result.unrecognized.append(
                ScanIssue(relative_path, f"stat_failed: {exc}")
            )
            return result
        result.entries.append(
            ScanEntry(
                storage_id=storage_id,
                relative_path=relative_path,
                absolute_path=str(path.resolve(strict=False)),
                kind=kind,
                size_bytes=int(stat.st_size),
                mtime_ns=int(stat.st_mtime_ns),
                action="compress" if kind == "tdms" else "register",
            )
        )
        return result

    def _scan_scope(
        self,
        *,
        storage_id: str,
        scope: str,
        scope_relative: str,
    ) -> tuple[Path, ScanResult]:
        """Scan only the requested database, folder, or file scope."""

        root = self.storage_roots.root(storage_id, require_available=True)
        if scope == "database":
            return root, scan_storage(
                storage_id,
                storage_roots=self.storage_roots,
                include_unrecognized=True,
            )
        if scope == "file":
            return root, self._scan_file(
                storage_id=storage_id,
                relative_path=scope_relative,
            )

        folder = self.storage_roots.resolve(storage_id, scope_relative)
        if not folder.exists():
            # A removed folder is an empty scan, so records below it are
            # reported as missing without walking the rest of the database.
            return root, ScanResult(storage_id=storage_id, scope=scope_relative)
        return root, scan_storage(
            storage_id,
            relative_dir=scope_relative,
            storage_roots=self.storage_roots,
            include_unrecognized=True,
        )

    def preview(
        self,
        *,
        storage_id: str = "wuxi_raw",
        scope: str = "database",
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        if scope not in {"database", "folder", "file"}:
            raise ValueError("scope must be database, folder, or file")
        if scope == "database":
            scope_relative = ""
        else:
            scope_relative = normalize_relative_path(str(relative_path or ""))

        def in_scope(path: str) -> bool:
            normalized = normalize_relative_path(path)
            if scope == "database":
                return True
            if scope == "file":
                return normalized == scope_relative
            return normalized == scope_relative or normalized.startswith(
                f"{scope_relative}/"
            )

        # Root availability is checked before the database is read. A missing
        # mount therefore cannot fan out into thousands of false misses. Folder
        # and file refreshes deliberately avoid walking the rest of the root.
        root, scan = self._scan_scope(
            storage_id=storage_id,
            scope=scope,
            scope_relative=scope_relative,
        )
        scanned: dict[str, dict[str, Any]] = {}
        bare_tdms: list[dict[str, Any]] = []
        for entry in scan.entries:
            payload = entry.to_dict()
            if entry.kind == "tdms":
                bare_tdms.append(payload)
                continue
            try:
                payload["fingerprint"] = fingerprint_file(
                    entry.absolute_path, kind=entry.kind
                ).to_dict()
            except Exception as exc:
                payload["fingerprint_error"] = f"{type(exc).__name__}: {exc}"
            scanned[entry.relative_path] = payload

        with self.database.connect(readonly=True) as connection:
            all_files = FileRepository(connection).list(
                record_status="active", limit=None
            )
        files = [
            record
            for record in all_files
            if record["storage_id"] == storage_id
            and in_scope(str(record["relative_path"]))
        ]

        items: list[dict[str, Any]] = []
        missing_records: list[dict[str, Any]] = []
        # Paths already represented by any database record are never emitted
        # as new registrations during a folder/file-scoped refresh.
        used_paths: set[str] = {
            str(record["relative_path"])
            for record in all_files
            if record["storage_id"] == storage_id
            and str(record["relative_path"]) in scanned
        }
        for record in files:
            relative = str(record["relative_path"])
            candidate = scanned.get(relative)
            if candidate is None:
                missing_records.append(record)
                continue
            fingerprint = candidate.get("fingerprint")
            if fingerprint is None:
                items.append(
                    self._item(
                        "conflict",
                        file_uid=record["file_uid"],
                        old_relative_path=relative,
                        message=candidate.get("fingerprint_error", "file is unreadable"),
                    )
                )
            elif fingerprint["payload_sha256"] != record["payload_sha256"]:
                items.append(
                    self._item(
                        "content_replaced",
                        file_uid=record["file_uid"],
                        old_relative_path=relative,
                        new_relative_path=relative,
                        old_payload_sha256=record["payload_sha256"],
                        fingerprint=fingerprint,
                        source_path=candidate["absolute_path"],
                    )
                )
            elif fingerprint["stored_sha256"] != record["stored_sha256"]:
                items.append(
                    self._item(
                        "recompressed",
                        file_uid=record["file_uid"],
                        old_relative_path=relative,
                        new_relative_path=relative,
                        fingerprint=fingerprint,
                    )
                )
            else:
                items.append(
                    self._item(
                        (
                            "restored"
                            if record["availability_status"] != "present"
                            or record["integrity_status"] != "verified"
                            else "unchanged"
                        ),
                        file_uid=record["file_uid"],
                        old_relative_path=relative,
                        fingerprint=fingerprint,
                    )
                )

        candidates_by_payload: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relative, entry in scanned.items():
            # A scoped refresh must never infer a move from a candidate outside
            # the selected folder/file.  Otherwise refreshing ``line-1`` could
            # silently rebind one of its missing records to an equal-payload
            # file under ``line-2``.
            if (
                relative in used_paths
                or not in_scope(relative)
                or "fingerprint" not in entry
            ):
                continue
            candidates_by_payload[entry["fingerprint"]["payload_sha256"]].append(entry)
        missing_by_payload: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in missing_records:
            missing_by_payload[str(record["payload_sha256"])].append(record)

        for payload_sha, records in missing_by_payload.items():
            candidates = candidates_by_payload.get(payload_sha, [])
            if len(records) == 1 and len(candidates) == 1:
                record = records[0]
                candidate = candidates[0]
                used_paths.add(str(candidate["relative_path"]))
                items.append(
                    self._item(
                        "move",
                        file_uid=record["file_uid"],
                        old_relative_path=record["relative_path"],
                        new_relative_path=candidate["relative_path"],
                        fingerprint=candidate["fingerprint"],
                    )
                )
            elif candidates:
                candidate_paths = [item["relative_path"] for item in candidates]
                used_paths.update(str(path) for path in candidate_paths)
                for record in records:
                    items.append(
                        self._item(
                            "ambiguous",
                            file_uid=record["file_uid"],
                            old_relative_path=record["relative_path"],
                            candidates=candidate_paths,
                            message="multiple same-payload records or candidate paths",
                        )
                    )
            else:
                for record in records:
                    items.append(
                        self._item(
                            "missing",
                            file_uid=record["file_uid"],
                            old_relative_path=record["relative_path"],
                        )
                    )

        for relative, entry in scanned.items():
            if relative in used_paths or not in_scope(relative):
                continue
            items.append(
                self._item(
                    "pending_register",
                    new_relative_path=relative,
                    source_path=entry["absolute_path"],
                    fingerprint=entry.get("fingerprint"),
                    message=entry.get("fingerprint_error"),
                )
            )
        for entry in bare_tdms:
            if not in_scope(str(entry["relative_path"])):
                continue
            items.append(
                self._item(
                    "pending_compress",
                    old_relative_path=entry["relative_path"],
                    new_relative_path=f"{entry['relative_path']}.zst",
                    source_path=entry["absolute_path"],
                )
            )

        order = {
            "content_replaced": 0,
            "conflict": 1,
            "ambiguous": 2,
            "move": 3,
            "missing": 4,
            "recompressed": 5,
            "restored": 6,
            "pending_compress": 7,
            "pending_register": 8,
            "unchanged": 9,
        }
        items.sort(
            key=lambda item: (
                order.get(item["category"], 99),
                str(item.get("old_relative_path") or item.get("new_relative_path") or "").casefold(),
            )
        )
        counts: dict[str, int] = defaultdict(int)
        for item in items:
            counts[item["category"]] += 1
        preview_id = str(uuid4())
        return {
            "preview_id": preview_id,
            "database_path": str(self.database.database_path),
            "storage_id": storage_id,
            "storage_root": str(root),
            "scope": scope,
            "scope_relative_path": scope_relative or None,
            "created_at": _utc_now(),
            "counts": dict(counts),
            "items": items,
            "unrecognized": [
                issue.to_dict()
                for issue in scan.unrecognized
                if in_scope(str(issue.relative_path))
            ],
            "excluded_count": scan.excluded_count,
        }

    @staticmethod
    def _normalize_conditions(values: Mapping[str, Any] | None) -> dict[str, Any]:
        # Keep reconciliation and ordinary import on the same fixed/custom
        # condition contract.  In particular, custom keys must be stored in
        # metadata_json.condition_extras rather than sent to FileRepository as
        # unknown SQL columns.
        return ImportService._normalize_conditions(values)

    def _register_replacement(
        self,
        item: Mapping[str, Any],
        *,
        storage_id: str,
        conditions: Mapping[str, Any] | None,
    ) -> str:
        source = self.storage_roots.resolve(
            storage_id,
            str(item["new_relative_path"]),
            require_exists=True,
        )
        kind = detect_file_kind(source)
        if kind not in {"tdms_zst", "wav"}:
            raise ReconcileConflict("replacement must be a registered file format")
        fingerprint = fingerprint_file(source, kind=kind)
        if fingerprint.payload_sha256 != item["fingerprint"]["payload_sha256"]:
            raise ReconcileConflict("replacement content changed after preview")
        profile = load_sample_profile(
            self.profiles_dir / "default.yaml" if self.profiles_dir else None
        )
        discovery = discover_samples(source, profile=profile)
        if discovery.status == "invalid" or (
            kind == "tdms_zst" and not discovery.ready
        ):
            raise ReconcileConflict(
                "; ".join(discovery.issues) or "sample discovery failed"
            )
        now = _utc_now()
        with self.database.transaction() as connection:
            files = FileRepository(connection)
            samples = SampleRepository(connection)
            old = self._require_preview_record(
                files, item, storage_id=storage_id
            )
            if old["payload_sha256"] != item["old_payload_sha256"]:
                raise ReconcileConflict("old file record changed after preview")
            inherited_conditions = {
                field: old.get(field) for field in CONDITION_FIELDS
            }
            raw_metadata = old.get("metadata_json")
            try:
                old_metadata = (
                    json.loads(raw_metadata)
                    if isinstance(raw_metadata, str)
                    else dict(raw_metadata or {})
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ReconcileConflict(
                    "old file metadata is invalid; cannot safely inherit conditions"
                ) from exc
            old_extras = old_metadata.get("condition_extras", {})
            if not isinstance(old_extras, Mapping):
                raise ReconcileConflict(
                    "old file condition_extras is invalid; cannot safely inherit conditions"
                )
            inherited_conditions["extra_fields"] = dict(old_extras)

            if conditions is not None:
                unknown = set(conditions) - CONDITION_FIELDS - {"extra_fields"}
                if unknown:
                    raise ValueError(
                        "unsupported condition fields: "
                        + ", ".join(sorted(unknown))
                    )
                for field in CONDITION_FIELDS:
                    if field in conditions:
                        inherited_conditions[field] = conditions[field]
                if "extra_fields" in conditions:
                    extra_patch = conditions["extra_fields"]
                    if not isinstance(extra_patch, Mapping):
                        raise TypeError("extra_fields must be an object")
                    merged_extras = dict(old_extras)
                    for key, value in extra_patch.items():
                        if value is None:
                            merged_extras.pop(key, None)
                        else:
                            merged_extras[key] = value
                    inherited_conditions["extra_fields"] = merged_extras

            normalized_conditions = self._normalize_conditions(inherited_conditions)
            condition_extras = dict(normalized_conditions.pop("extra_fields", {}))
            files.supersede(str(old["file_uid"]))
            metadata = {
                "source_kind": kind,
                "sample_discovery_status": discovery.status,
                "sample_issues": list(discovery.issues),
                "validated_metadata": discovery.file_metadata,
                "supersedes_file_uid": old["file_uid"],
            }
            if condition_extras:
                metadata["condition_extras"] = condition_extras
            created = files.create(
                {
                    "storage_id": old["storage_id"],
                    "relative_path": old["relative_path"],
                    **normalized_conditions,
                    **fingerprint.to_dict(),
                    "last_seen_at": now,
                    "last_verified_at": now,
                    "metadata_json": metadata,
                }
            )
            for sample in discovery.samples:
                samples.create({"file_uid": created["file_uid"], **sample.to_dict()})
        return str(created["file_uid"])

    def apply(
        self,
        preview: Mapping[str, Any],
        actions: Sequence[Mapping[str, Any]],
        *,
        replacement_conditions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if Path(str(preview.get("database_path"))).resolve() != self.database.database_path:
            raise ReconcileConflict("preview belongs to another database")
        storage_id = str(preview["storage_id"])
        self.storage_roots.root(storage_id, require_available=True)
        by_id = {str(item["item_id"]): item for item in preview.get("items", [])}
        results: list[dict[str, Any]] = []
        importer = ImportService(
            self.database.database_path,
            storage_roots=self.storage_roots,
            profiles_dir=self.profiles_dir,
        )
        for requested in actions:
            item_id = str(requested.get("item_id") or "")
            action = str(requested.get("action") or "")
            item = by_id.get(item_id)
            if item is None:
                raise ReconcileConflict(f"preview item not found: {item_id}")
            category = str(item["category"])
            result: dict[str, Any] = {
                "item_id": item_id,
                "category": category,
                "action": action,
            }
            if action == "manual_review":
                result["status"] = "left_for_manual_review"
            elif action in {"register", "compress_and_register"}:
                expected = (
                    "pending_register" if action == "register" else "pending_compress"
                )
                if category != expected:
                    raise ReconcileConflict(f"{action} does not match {category}")
                batch = importer.import_paths(
                    [str(item["source_path"])],
                    target_storage_id=storage_id,
                    conditions=replacement_conditions or {},
                )
                result["status"] = batch["run"]["status"]
                result["run_uuid"] = batch["run"]["run_uuid"]
            elif action == "update_path":
                if category not in {"move", "ambiguous"}:
                    raise ReconcileConflict("update_path requires a move or ambiguous item")
                selected_relative = str(
                    requested.get("target_relative_path")
                    or item.get("new_relative_path")
                    or ""
                )
                if category == "ambiguous" and selected_relative not in item.get(
                    "candidates", []
                ):
                    raise ReconcileConflict("selected path is not a preview candidate")
                new_path = self.storage_roots.resolve(
                    storage_id, selected_relative, require_exists=True
                )
                fingerprint = fingerprint_file(new_path, kind=detect_file_kind(new_path))
                expected_payload = (
                    item["fingerprint"]["payload_sha256"]
                    if category == "move"
                    else None
                )
                if expected_payload is not None and fingerprint.payload_sha256 != expected_payload:
                    raise ReconcileConflict("move candidate changed after preview")
                with self.database.transaction() as connection:
                    files = FileRepository(connection)
                    old = self._require_preview_record(
                        files, item, storage_id=storage_id
                    )
                    if fingerprint.payload_sha256 != old["payload_sha256"]:
                        raise ReconcileConflict("selected path has different logical content")
                    if self.storage_roots.resolve(storage_id, old["relative_path"]).exists():
                        raise ReconcileConflict("old path exists again; rescan before applying")
                    files.update(
                        old["file_uid"],
                        {
                            "relative_path": selected_relative,
                            **fingerprint.to_dict(),
                            "availability_status": "present",
                            "integrity_status": "verified",
                            "last_seen_at": _utc_now(),
                            "last_verified_at": _utc_now(),
                        },
                    )
                result["status"] = "applied"
            elif action == "mark_missing":
                if category != "missing":
                    raise ReconcileConflict("mark_missing requires a missing item")
                with self.database.transaction() as connection:
                    files = FileRepository(connection)
                    samples = SampleRepository(connection)
                    record = self._require_preview_record(
                        files, item, storage_id=storage_id
                    )
                    if self.storage_roots.resolve(storage_id, record["relative_path"]).exists():
                        raise ReconcileConflict("file exists again; rescan before applying")
                    files.mark_missing(record["file_uid"], seen_at=_utc_now())
                    for sample in samples.list_by_file(record["file_uid"]):
                        samples.update(
                            record["file_uid"],
                            sample["sample_id"],
                            {"availability_status": "missing"},
                        )
                result["status"] = "applied"
            elif action == "mark_present":
                if category != "restored":
                    raise ReconcileConflict("mark_present requires a restored item")
                path = self.storage_roots.resolve(
                    storage_id, str(item["old_relative_path"]), require_exists=True
                )
                fingerprint = fingerprint_file(path, kind=detect_file_kind(path))
                with self.database.transaction() as connection:
                    files = FileRepository(connection)
                    samples = SampleRepository(connection)
                    record = self._require_preview_record(
                        files, item, storage_id=storage_id
                    )
                    if fingerprint.payload_sha256 != record["payload_sha256"]:
                        raise ReconcileConflict("restored file content does not match its record")
                    files.update(
                        record["file_uid"],
                        {
                            **fingerprint.to_dict(),
                            "availability_status": "present",
                            "integrity_status": "verified",
                            "last_seen_at": _utc_now(),
                            "last_verified_at": _utc_now(),
                        },
                    )
                    for sample in samples.list_by_file(record["file_uid"]):
                        samples.update(
                            record["file_uid"],
                            sample["sample_id"],
                            {"availability_status": "present"},
                        )
                result["status"] = "applied"
            elif action == "accept_recompression":
                if category != "recompressed":
                    raise ReconcileConflict(
                        "accept_recompression requires a recompressed item"
                    )
                path = self.storage_roots.resolve(
                    storage_id, str(item["old_relative_path"]), require_exists=True
                )
                fingerprint = fingerprint_file(path, kind=detect_file_kind(path))
                with self.database.transaction() as connection:
                    files = FileRepository(connection)
                    record = self._require_preview_record(
                        files, item, storage_id=storage_id
                    )
                    if fingerprint.payload_sha256 != record["payload_sha256"]:
                        raise ReconcileConflict("logical payload changed; rescan required")
                    files.update(
                        record["file_uid"],
                        {
                            **fingerprint.to_dict(),
                            "availability_status": "present",
                            "integrity_status": "verified",
                            "last_seen_at": _utc_now(),
                            "last_verified_at": _utc_now(),
                        },
                    )
                result["status"] = "applied"
            elif action == "supersede_and_register":
                if category != "content_replaced":
                    raise ReconcileConflict(
                        "supersede_and_register requires a content replacement item"
                    )
                result["new_file_uid"] = self._register_replacement(
                    item,
                    storage_id=storage_id,
                    conditions=replacement_conditions,
                )
                result["status"] = "applied"
            else:
                raise ValueError(f"unsupported reconcile action: {action}")
            results.append(result)
        return {
            "preview_id": preview["preview_id"],
            "applied_count": sum(
                1 for item in results if item.get("status") == "applied"
            ),
            "results": results,
            "message": f"已处理 {len(results)} 项目录变更",
        }


__all__ = ["ReconcileConflict", "ReconcileService"]
