"""UI-independent entry points for raw-data maintenance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.files import StorageRoots, normalize_relative_path

from .condition_service import ConditionService
from .database_service import DatabaseService
from .import_service import ImportService
from .reconcile_service import ReconcileService
from ..repositories.files import FileRepository


class DataMaintenanceService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        storage_roots: StorageRoots | Mapping[str, str | Path],
        profiles_dir: str | Path | None = None,
    ) -> None:
        self.database = DatabaseService(database_path)
        self.storage_roots = storage_roots if isinstance(storage_roots, StorageRoots) else StorageRoots(storage_roots)
        self.profiles_dir = profiles_dir

    @staticmethod
    def _line_name(value: str) -> str:
        line = value.strip()
        if not line or line in {".", ".."} or any(character in line for character in "/\\:"):
            raise ValueError("产线名称必须是单个安全的文件夹名称")
        if line.startswith(".") or line.casefold() in {"databases", "backups", "exports", "logs", "_ai3"}:
            raise ValueError("该产线名称为系统保留名称")
        return line

    def export_files_by_line(self) -> dict[str, Any]:
        """Export active file records as one JSON array per production line."""
        database_dir = self.database.database_path.parent
        export_dir = database_dir / "exports"
        if export_dir.is_symlink():
            raise ValueError("导出目录不能是符号链接")
        export_dir.mkdir(exist_ok=True)
        root = database_dir.parent
        lines = DatabaseService.read_lines(root) or []
        grouped: dict[str, list[dict[str, Any]]] = {self._line_name(line): [] for line in lines}
        with self.database.connect(readonly=True) as connection:
            for record in FileRepository(connection).list(limit=None):
                line = self._line_name(str(record.get("line") or ""))
                metadata = json.loads(record["metadata_json"] or "{}")
                grouped.setdefault(line, []).append({
                    "file_uid": record["file_uid"],
                    "storage_id": record["storage_id"],
                    "relative_path": record["relative_path"],
                    "conditions": {
                        "line": line,
                        "device_id": record["device_id"],
                        "model_name": record["model_name"],
                        "reference": record["reference"],
                        "load_value": record["load_value"],
                        "load_unit": record["load_unit"],
                        "speed_ratio": record["speed_ratio"],
                        "timestamp": record["acquired_at"],
                    },
                    "metadata": metadata,
                })
        exported = []
        for line, records in grouped.items():
            target = export_dir / f"{line}.json"
            if target.is_symlink():
                raise ValueError(f"导出文件不能是符号链接: {target}")
            temporary = export_dir / f".{uuid4().hex}.tmp"
            try:
                temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            exported.append({"line": line, "path": str(target), "file_count": len(records)})
        return {"export_directory": str(export_dir), "line_count": len(exported), "file_count": sum(len(records) for records in grouped.values()), "lines": exported}

    def validate_import_line(
        self,
        *,
        line: str,
        known_lines: Sequence[str],
        source_paths: Sequence[str],
        source_scope: str | None,
        target_storage_id: str,
        target_relative_dir: str,
    ) -> str:
        selected = self._line_name(line)
        saved = next((item for item in known_lines if item.casefold() == selected.casefold()), None)
        if saved is None:
            raise ValueError("请选择数据库中已有的产线")
        if source_scope == "outside":
            if normalize_relative_path(target_relative_dir, allow_empty=True) != saved:
                raise ValueError("复制或移动的目标目录必须是所选产线文件夹")
        elif source_scope == "inside":
            root = self.storage_roots.root(target_storage_id).resolve(strict=False)
            for raw_path in source_paths:
                source = Path(raw_path).expanduser().resolve(strict=False)
                try:
                    relative = source.relative_to(root)
                except ValueError as exc:
                    raise ValueError("不拷贝的文件必须已经位于当前数据库文件夹内") from exc
                if not relative.parts or relative.parts[0].casefold() != saved.casefold():
                    raise ValueError(f"不拷贝的文件必须位于产线文件夹 {saved} 内")
        return saved

    @staticmethod
    def _preview_signature(result: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        signature = []
        for item in result.get("items", []):
            samples = tuple(
                (sample.get("sample_id"), json.dumps(sample.get("locator_json", {}), sort_keys=True))
                for sample in item.get("samples", [])
            )
            signature.append((
                item.get("source_display_path"), item.get("target_relative_path"),
                item.get("status"), item.get("payload_sha256"), samples,
            ))
        return signature

    def _importer(self) -> ImportService:
        return ImportService(
            self.database.database_path,
            storage_roots=self.storage_roots,
            profiles_dir=self.profiles_dir,
        )

    def preview_import(self, *, known_lines: Sequence[str], **options: Any) -> dict[str, Any]:
        self.validate_import_line(
            line=str(options["conditions"].get("line") or ""),
            known_lines=known_lines,
            source_paths=options["source_paths"],
            source_scope=options.get("expected_source_scope"),
            target_storage_id=options["target_storage_id"],
            target_relative_dir=options["target_relative_dir"],
        )
        return self._importer().preview_paths(**options)

    def apply_import(
        self,
        *,
        known_lines: Sequence[str],
        preview: Mapping[str, Any],
        **options: Any,
    ) -> dict[str, Any]:
        fresh = self.preview_import(known_lines=known_lines, **options)
        if self._preview_signature(fresh) != self._preview_signature(preview):
            raise ValueError("文件、样本或目标状态已变化，请重新生成导入预览")
        planned_ids = {
            str(item["target_relative_path"]): str(item["file_uid"])
            for item in preview.get("items", [])
            if item.get("target_relative_path") and item.get("file_uid")
        }
        return self._importer().import_paths(**options, planned_file_uids=planned_ids)

    def update_conditions(
        self,
        conditions: Mapping[str, Any],
        *,
        file_uids: Sequence[str] | None = None,
        scope: str | None = None,
        relative_path: str | None = None,
    ) -> int:
        with self.database.connect() as connection:
            return ConditionService(connection).update_scope(
                conditions, file_uids=file_uids, scope=scope, relative_path=relative_path,
            )

    def _reconciler(self) -> ReconcileService:
        return ReconcileService(
            self.database.database_path,
            storage_roots=self.storage_roots,
            profiles_dir=self.profiles_dir,
        )

    def preview_paths(self, *, storage_id: str, scope: str, relative_path: str | None = None) -> dict[str, Any]:
        return self._reconciler().preview(storage_id=storage_id, scope=scope, relative_path=relative_path)

    def apply_paths(
        self,
        preview: Mapping[str, Any],
        actions: Sequence[Mapping[str, Any]],
        *,
        replacement_conditions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._reconciler().apply(preview, actions, replacement_conditions=replacement_conditions)
