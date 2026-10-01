"""FastAPI entry point for the standalone AI-3.0 phase-1 application."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import io
import os
import re
from pathlib import Path
from functools import wraps
import shutil
import sqlite3
import subprocess
import sys
import wave
from datetime import datetime
from threading import RLock
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import numpy as np

from core.algorithms.features.mel_spectrogram import extract as extract_mel_spectrogram
from core.algorithms.features.mfcc import extract as extract_mfcc
from core.algorithms.features.pcen import extract as extract_pcen
from core.algorithms.features.power_spectrum import extract as extract_power_spectrum
from core.algorithms.preprocess.cut_rms import apply as cut_and_normalize_rms
from core.algorithms.preprocess.highpass import apply as highpass_filter

from core.database import (
    ConditionService,
    DataMaintenanceService,
    DatabaseService,
    FileRepository,
    ImportService,
    SampleRepository,
)
from core.files import (
    StorageRoots,
    StorageUnavailableError,
    normalize_relative_path,
    scan_storage,
)
from core.files.readers import describe_wav, read_signal
from core.files.sample_discovery.line_rules import load_line_rules
from core.files.sample_discovery import discover_tdms_samples

from .models import (
    ConditionUpdateRequest,
    DatabaseCreateRequest,
    DatabaseCreateFromFolderRequest,
    DatabaseFolderSelectRequest,
    DatabaseLineCreateRequest,
    DatabaseOpenRequest,
    DatabaseUpdateRequest,
    ImportRequest,
    LabelEventCreateRequest,
    LabelEventDeleteRequest,
    LabelProjectSaveRequest,
    LabelSessionCreateRequest,
    LabelTaxonomyUpdateRequest,
    ReconcileApplyRequest,
    StorageFolderSelectRequest,
    TdmsLabelResolveRequest,
    SignalAnalysisRequest,
)
from .label_analysis import CARD_SPECS, build_analysis
from .runtime import PROJECT_ROOT, USER_DATA_ROOT, state
from .waveform_cache import waveform_cache


UI_DIR = PROJECT_ROOT / "web" / "ui"
LEGACY_STORAGE_ID = "wuxi_raw"
CONDITION_OPTION_FIELDS = (
    "line",
    "device_id",
    "reference",
    "model_name",
    "load_value",
    "load_unit",
    "speed_ratio",
    "acquired_at",
)
LABEL_RESULTS = {
    "ok": {"id": 0, "name": "正常"},
    "nok": {"id": 1, "name": "异常"},
    "boundary": {"id": 2, "name": "边界"},
}
LABEL_REASONS = {
    "noisy_normal": {"id": -1, "name": "干扰", "parent": "ok"},
    "clean_normal": {"id": 0, "name": "正常", "parent": "ok"},
    "boundary": {"id": 2, "name": "边界", "parent": "boundary"},
    "unlabeled": {"id": 101, "name": "未标注", "parent": "nok"},
    "tick_tock": {"id": 102, "name": "秒表", "parent": "nok"},
    "friction": {"id": 103, "name": "摩擦", "parent": "nok"},
    "friction_acc": {"id": 104, "name": "摩擦acc", "parent": "nok"},
    "noise": {"id": 105, "name": "杂音", "parent": "nok"},
    "gear_chatter": {"id": 106, "name": "咬齿", "parent": "nok"},
    "chatter": {"id": 107, "name": "震颤", "parent": "nok"},
    "mada": {"id": 108, "name": "马达", "parent": "nok"},
    "dada": {"id": 109, "name": "哒哒_咔咔", "parent": "nok"},
    "other": {"id": 199, "name": "其它", "parent": "nok"},
}


def _default_label_taxonomy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "results": [
            {"result_key": key, "result_id": value["id"], "result_name": value["name"]}
            for key, value in LABEL_RESULTS.items()
        ],
        "reasons": [
            {
                "reason_key": key,
                "reason_id": value["id"],
                "reason_name": value["name"],
                "result_key": value["parent"],
            }
            for key, value in LABEL_REASONS.items()
        ],
    }


def _label_taxonomy_path(source: Path) -> Path:
    if source.suffix.lower() == ".json":
        return source
    return (source if source.is_dir() else source.parent) / "label_setting.json"


def _validate_label_taxonomy(payload: Mapping[str, Any]) -> dict[str, Any]:
    results = payload.get("results")
    reasons = payload.get("reasons")
    if not isinstance(results, list) or not results:
        raise ValueError("至少保留一个 result")
    if not isinstance(reasons, list):
        raise ValueError("reasons 必须是列表")
    result_keys: set[str] = set()
    normalized_results = []
    for item in results:
        key = str(item.get("result_key", "")).strip()
        name = str(item.get("result_name", "")).strip()
        if not key or not name or key in result_keys:
            raise ValueError("result 的 key/name 不能为空，且 key 不可重复")
        result_keys.add(key)
        normalized_results.append({"result_key": key, "result_id": int(item["result_id"]), "result_name": name})
    reason_keys: set[str] = set()
    normalized_reasons = []
    for item in reasons:
        key = str(item.get("reason_key", "")).strip()
        name = str(item.get("reason_name", "")).strip()
        parent = str(item.get("result_key", "")).strip()
        if not key or not name or key in reason_keys:
            raise ValueError("reason 的 key/name 不能为空，且 key 不可重复")
        if parent not in result_keys:
            raise ValueError(f"reason {key} 关联的 result 不存在")
        reason_keys.add(key)
        normalized_reasons.append({"reason_key": key, "reason_id": int(item["reason_id"]), "reason_name": name, "result_key": parent})
    return {"schema_version": 1, "results": normalized_results, "reasons": normalized_reasons}


def _load_label_taxonomy(source: Path) -> tuple[Path, dict[str, Any]]:
    path = _label_taxonomy_path(source)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        taxonomy = _validate_label_taxonomy(payload)
    else:
        taxonomy = _default_label_taxonomy()
        if source.is_dir():
            _write_standalone_labels(path, taxonomy)
    return path, taxonomy


def _taxonomy_maps(taxonomy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "results": {item["result_key"]: {"id": item["result_id"], "name": item["result_name"]} for item in taxonomy["results"]},
        "reasons": {item["reason_key"]: {"id": item["reason_id"], "name": item["reason_name"], "parent": item["result_key"]} for item in taxonomy["reasons"]},
    }

app = FastAPI(title="AI-3.0 Data Management", version="0.1.0")
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.middleware("http")
async def prevent_labeling_browser_cache(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path.startswith("/api/labeling/"):
        response.headers["Cache-Control"] = "no-store"
    return response


database_operation_lock = RLock()


def _serialized_database_operation(function: Any) -> Any:
    """Keep database-root migration and database writes mutually exclusive."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with database_operation_lock:
            return function(*args, **kwargs)

    return wrapped


def _roots() -> StorageRoots:
    roots = dict(state.storage_roots)
    if not roots:
        raise HTTPException(status_code=409, detail="尚未选择数据文件夹，请先在数据库详情页选择")
    return StorageRoots(roots)


def _active_storage_id() -> str:
    if state.current_database is not None:
        current = state.current_database.resolve(strict=False)
        for record in state.list_registered_databases():
            if Path(record["path"]).expanduser().resolve(strict=False) == current:
                return str(record.get("storage_id") or LEGACY_STORAGE_ID)
    if state.storage_roots:
        return next(iter(state.storage_roots))
    return LEGACY_STORAGE_ID


def _catalog_record(database_uid: str) -> dict[str, Any] | None:
    return next(
        (item for item in state.list_registered_databases() if item["database_uid"] == database_uid),
        None,
    )


def _sync_database_lines(database_uid: str, root: Path) -> list[str]:
    """Keep the portable folder JSON and the local catalog in agreement."""
    saved = _catalog_record(database_uid) or {}
    lines = DatabaseService.migrate_lines(root)
    if lines is None:
        lines = list(saved.get("lines", []))
        DatabaseService.write_lines(root, lines)
    for line in lines:
        state.add_database_line(database_uid, _normalize_line_name(line))
    return lines


def _storage_root(*, require_available: bool = False) -> Path:
    selected = state.storage_root(_active_storage_id())
    if selected is None:
        raise HTTPException(status_code=409, detail="尚未选择数据文件夹，请先在数据库详情页选择")
    try:
        return _roots().root(_active_storage_id(), require_available=require_available)
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _require_database() -> Path:
    try:
        return state.require_database()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _database_path(database_uid: str | None = None) -> Path:
    """Return an explicitly selected database or the legacy active database."""

    if database_uid is None:
        return _require_database()
    return _resolve_database_uid(database_uid).path


def _json_value(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _business_file_payload(
    connection: sqlite3.Connection,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    metadata_internal = _json_value(record.get("metadata_json"), {})
    metadata = dict(metadata_internal.get("condition_extras") or {})
    conditions = {
        "line": record.get("line"),
        "device_id": record.get("device_id"),
        "model_name": record.get("model_name"),
        "reference": record.get("reference"),
        "load_value": record.get("load_value"),
        "load_unit": record.get("load_unit"),
        "speed_ratio": record.get("speed_ratio"),
        "timestamp": record.get("acquired_at"),
    }
    sample_rows = connection.execute(
        """
        SELECT sample_id, sample_scope
        FROM sample_records
        WHERE file_uid = ?
        ORDER BY sample_id
        """,
        (record["file_uid"],),
    ).fetchall()
    samples: list[dict[str, Any]] = []
    for sample_row in sample_rows:
        events = [
            {
                key: event[key]
                for key in (
                    "event_uuid", "source", "result_key", "result_id",
                    "result_name", "result_confidence", "reason_key",
                    "reason_id", "reason_name", "reason_confidence",
                    "timestamp", "note",
                )
            }
            for event in connection.execute(
                """
                SELECT * FROM label_events
                WHERE file_uid = ? AND sample_id = ?
                ORDER BY timestamp, event_uuid
                """,
                (record["file_uid"], sample_row["sample_id"]),
            ).fetchall()
        ]
        samples.append(
            {
                "sample_id": sample_row["sample_id"],
                "sample_scope": _json_value(sample_row["sample_scope"], {}),
                "label_events": events,
            }
        )
    return {
        "file_uid": record["file_uid"],
        "storage_id": record["storage_id"],
        "relative_path": record["relative_path"],
        "conditions": conditions,
        "metadata": metadata,
        "samples": samples,
    }


def _label_file_record(database_uid: str, raw_path: str) -> tuple[Any, Path, dict[str, Any]]:
    selected = _resolve_database_uid(database_uid)
    record = _catalog_record(database_uid) or {}
    root = Path(
        record.get("dataset_root") or selected.storage_root or _database_root_from_path(selected.path)
    ).expanduser().resolve(strict=True)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        absolute = candidate.resolve(strict=False)
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("TDMS 文件必须位于当前数据库文件夹内") from exc
    else:
        relative = normalize_relative_path(raw_path)
        absolute = (root / relative).resolve(strict=False)
    with DatabaseService(selected.path).connect(readonly=True) as connection:
        repository = FileRepository(connection)
        file_record = repository.get_by_path(selected.default_storage_id, relative)
        if file_record is None and relative.lower().endswith(".tdms"):
            relative = f"{relative}.zst"
            absolute = (root / relative).resolve(strict=False)
            file_record = repository.get_by_path(selected.default_storage_id, relative)
    if file_record is None:
        raise FileNotFoundError("该 TDMS 尚未登记到当前数据库，请先通过数据维护导入")
    if not absolute.is_file():
        raise FileNotFoundError(f"TDMS 文件当前不可访问: {absolute}")
    return selected, absolute, file_record


def _standalone_label_path(source: Path) -> Path:
    lowered = source.name.lower()
    if lowered.endswith(".tdms.zst"):
        stem = source.name[:-9]
    elif lowered.endswith(".tdms"):
        stem = source.name[:-5]
    elif lowered.endswith(".wav"):
        stem = source.name[:-4]
    else:
        raise ValueError("请选择 .tdms、.tdms.zst 或 .wav 文件")
    return source.with_name(f"{stem}.labels.json")


def _read_standalone_labels(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"标注文件不是 JSON 对象: {path}")
    return payload


def _write_standalone_labels(path: Path, document: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _business_import_preview(
    result: Mapping[str, Any],
    request: ImportRequest,
) -> list[dict[str, Any]]:
    raw_conditions = request.conditions.model_dump()
    metadata = dict(raw_conditions.pop("extra_fields", {}) or {})
    conditions = {
        "line": raw_conditions.get("line"),
        "device_id": raw_conditions.get("device_id"),
        "model_name": raw_conditions.get("model_name"),
        "reference": raw_conditions.get("reference"),
        "load_value": raw_conditions.get("load_value"),
        "load_unit": raw_conditions.get("load_unit"),
        "speed_ratio": raw_conditions.get("speed_ratio"),
        "timestamp": raw_conditions.get("acquired_at"),
    }
    documents: list[dict[str, Any]] = []
    for item in result.get("items", []):
        relative_path = item.get("target_relative_path")
        file_uid = item.get("file_uid")
        if not relative_path or not file_uid:
            continue
        samples = []
        for sample in item.get("samples", []):
            locator = sample.get("locator_json") or {}
            start_s = float(locator.get("start_s", 0.0))
            end_value = locator.get("end_s", sample.get("duration_s"))
            end_s = float(end_value) if end_value is not None else start_s
            samples.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "sample_scope": {"start_s": start_s, "end_s": end_s},
                    "label_events": [],
                }
            )
        documents.append(
            {
                "file_uid": str(file_uid),
                "storage_id": request.target_storage_id,
                "relative_path": str(relative_path),
                "conditions": dict(conditions),
                "metadata": dict(metadata),
                "samples": samples,
            }
        )
    return documents


def _database_payload(info: Any, *, active: bool = False) -> dict[str, Any]:
    database_root = info.storage_root or _database_root_from_path(info.path)
    catalog_record = next(
        (item for item in state.list_registered_databases() if item["database_uid"] == info.database_uid),
        {},
    )
    return {
        "database_uid": info.database_uid,
        "database_name": info.database_name,
        "default_storage_id": info.default_storage_id,
        "schema_version": info.schema_version,
        "path": str(info.path),
        "sqlite_path": str(info.path),
        "database_root": str(database_root),
        "lines": list(catalog_record.get("lines", [])),
        "active": active,
    }


def _pending_database_payload(record: Mapping[str, Any], *, active: bool = False) -> dict[str, Any]:
    return {
        "database_uid": str(record["database_uid"]),
        "database_name": str(record["database_name"]),
        "default_storage_id": LEGACY_STORAGE_ID,
        "schema_version": None,
        "path": None,
        "sqlite_path": None,
        "database_root": None,
        "pending_path": True,
        "lines": [],
        "active": active,
    }


def _database_root_from_path(path: str | Path) -> Path:
    """Resolve ``<root>`` from current or legacy database layouts."""

    target = Path(path).expanduser().resolve(strict=False)
    if target.parent.name.casefold() != "databases":
        raise ValueError("database is not inside <database_root>/databases")
    if target.parent.parent.name.casefold() == "_ai3":
        return target.parent.parent.parent
    return target.parent.parent


def _managed_database_roots() -> tuple[Path, ...]:
    """Return configured roots plus one-level self-contained database roots."""

    roots: list[Path] = []
    seen: set[Path] = set()
    for configured in state.catalog_roots():
        root = configured.resolve(strict=False)
        candidates = [root]
        if root.is_dir() and not root.is_symlink():
            try:
                candidates.extend(
                    child.resolve(strict=False)
                    for child in root.iterdir()
                    if child.is_dir()
                    and not child.is_symlink()
                    and not child.name.startswith(".")
                    and (
                        (child / "databases").is_dir()
                        or (child / "_ai3" / "databases").is_dir()
                    )
                )
            except OSError:
                pass
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            roots.append(candidate)
    return tuple(roots)


def _database_records_for_root(root: Path) -> list[dict[str, Any]]:
    resolved_root = root.resolve(strict=False)
    records: list[dict[str, Any]] = []
    database_dirs = [resolved_root / "databases", resolved_root / "_ai3" / "databases"]
    paths: list[Path] = []
    for database_dir in database_dirs:
        if database_dir.is_symlink() or not database_dir.is_dir():
            continue
        try:
            database_dir.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        paths.extend(database_dir.glob("*.sqlite3"))
    for path in sorted(paths, key=lambda item: str(item).casefold()):
        # FAT/exFAT volumes may contain macOS AppleDouble sidecars such as
        # ``._name.sqlite3``.  They are metadata, not databases.
        if path.name.startswith("._") or path.is_symlink():
            continue
        try:
            info = DatabaseService.open_database(
                path,
                storage_root=root,
                expected_storage_id=None,
                require_storage=False,
            )
            records.append(
                _database_payload(
                    info,
                    active=(
                        state.current_database is not None
                        and path.resolve() == state.current_database
                    ),
                )
            )
        except Exception as exc:
            records.append({"path": str(path), "database_name": path.stem, "error": str(exc)})
    return records


def _database_records() -> list[dict[str, Any]]:
    return DatabaseService.list_catalog_records(
        state.list_pending_databases(),
        state.list_registered_databases(),
        current_database=state.current_database,
        default_storage_id=LEGACY_STORAGE_ID,
    )


def _resolve_database_uid(database_uid: str) -> Any:
    saved = next(
        (item for item in state.list_registered_databases() if item["database_uid"] == database_uid),
        None,
    )
    if saved is None:
        raise FileNotFoundError(f"database_uid was not found in catalog_state.json: {database_uid}")
    path = Path(saved["path"]).expanduser().resolve(strict=True)
    root = Path(saved.get("dataset_root") or _database_root_from_path(path)).expanduser().resolve(strict=False)
    storage_id = str(saved.get("storage_id") or LEGACY_STORAGE_ID)
    return DatabaseService.open_database(
        path, storage_root=root, expected_storage_id=storage_id, require_storage=False
    )


def _normalize_line_name(value: str) -> str:
    line = value.strip()
    if not line or line in {".", ".."} or any(character in line for character in "/\\:"):
        raise ValueError("产线名称必须是单个安全的文件夹名称")
    if line.startswith(".") or line.casefold() in {"databases", "backups", "exports", "logs", "_ai3"}:
        raise ValueError("该产线名称为系统保留名称")
    return line


def _maintenance_service(
    database_uid: str | None = None, *, require_storage: bool = True,
) -> DataMaintenanceService:
    if database_uid is None:
        database_path = _require_database()
        roots = _roots() if require_storage else StorageRoots(
            state.storage_roots or {_active_storage_id(): _database_root_from_path(database_path)}
        )
        return DataMaintenanceService(
            database_path, storage_roots=roots, profiles_dir=state.profiles_dir,
        )
    selected = _resolve_database_uid(database_uid)
    root = selected.storage_root or _database_root_from_path(selected.path)
    return DataMaintenanceService(
        selected.path,
        storage_roots=StorageRoots({selected.default_storage_id: root}),
        profiles_dir=state.profiles_dir,
    )


def _active_import_lines() -> list[str]:
    active_database = _require_database()
    record = next(
        (
            item for item in state.list_registered_databases()
            if Path(item["path"]).expanduser().resolve(strict=False) == active_database
        ),
        None,
    )
    if record is None:
        raise ValueError("当前数据库不在 catalog_state.json 中")
    return list(record.get("lines", []))


def _import_options(request: ImportRequest) -> dict[str, Any]:
    return {
        "source_paths": request.source_paths,
        "target_storage_id": request.target_storage_id,
        "target_relative_dir": request.target_relative_dir,
        "expected_source_scope": request.source_scope,
        "transfer_mode": request.transfer_mode,
        "expected_file_kind": request.source_file_type,
        "conditions": request.conditions.model_dump(),
        "sample_profile": request.sample_profile,
        "channel_mappings": [item.model_dump() for item in request.channel_mappings] if request.channel_mappings else None,
    }


def _catalog_root_for_database(path: str | Path) -> tuple[Path, Path]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"database must not be a symlink: {candidate}")
    target = candidate.resolve(strict=True)
    if not target.is_file() or target.suffix.casefold() != ".sqlite3":
        raise FileNotFoundError(f"database does not exist: {target}")
    for root_value in _managed_database_roots():
        root = root_value.resolve(strict=False)
        for database_dir in (root / "databases", root / "_ai3" / "databases"):
            if database_dir.is_symlink() or not database_dir.is_dir():
                continue
            if target.parent == database_dir.resolve():
                return root, target
    raise ValueError("database must be a direct SQLite file in a managed catalog")


def _normalize_selected_root(path: str | Path) -> Path:
    selected = Path(path).expanduser()
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    if not selected.exists() or not selected.is_dir():
        raise NotADirectoryError(f"所选数据目录不存在或不是文件夹: {selected}")
    if selected.is_symlink():
        raise ValueError(f"所选目录不能是符号链接: {selected}")
    selected = selected.resolve(strict=True)
    if selected.name.casefold() == "databases":
        if selected.parent.name.casefold() == "_ai3":
            return selected.parent.parent
        return selected.parent
    if selected.name.casefold() == "_ai3":
        return selected.parent
    return selected


def _choose_folder_macos(prompt: str = "选择 AI-3.0 数据文件夹") -> Path | None:
    if sys.platform == "win32":
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            value = filedialog.askdirectory(parent=root, title=prompt, mustexist=True)
            return Path(value) if value else None
        finally:
            root.destroy()
    if sys.platform != "darwin":
        raise RuntimeError("当前系统不支持本地文件夹选择器，请显式提供 path")
    command = [
        "osascript",
        "-e",
        f'POSIX path of (choose folder with prompt "{prompt}")',
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "-128" in message or "User canceled" in message:
            return None
        raise RuntimeError(message or "无法打开文件夹选择器")
    value = result.stdout.strip()
    return Path(value) if value else None


def _choose_files_macos(prompt: str = "选择要导入的原始文件") -> list[Path] | None:
    if sys.platform == "win32":
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            values = filedialog.askopenfilenames(parent=root, title=prompt)
            return [Path(value) for value in values] if values else None
        finally:
            root.destroy()
    if sys.platform != "darwin":
        raise RuntimeError("当前系统不支持本地文件选择器")
    script = f'''
set selectedFiles to choose file with prompt "{prompt}" with multiple selections allowed
set outputText to ""
repeat with selectedFile in selectedFiles
    set outputText to outputText & POSIX path of selectedFile & linefeed
end repeat
return outputText
'''
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "-128" in message or "User canceled" in message:
            return None
        raise RuntimeError(message or "无法打开文件选择器")
    return [Path(value) for value in result.stdout.splitlines() if value.strip()]


def _database_has_files(database_path: str | Path) -> bool:
    with DatabaseService(database_path).connect(readonly=True) as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()
    return bool(row and int(row["total"]) > 0)


def _migrate_empty_database_root(
    database_path: Path,
    destination_root: Path,
) -> tuple[Any, Path]:
    """Copy, validate, then switch one empty self-contained database root.

    The previous root is retained as a hidden recovery copy.  It no longer
    matches the managed-root discovery convention, so the same database UID
    cannot be opened twice by accident.
    """

    source_root = _database_root_from_path(database_path).resolve(strict=True)
    destination = destination_root.expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if destination.is_symlink():
        raise ValueError("新数据库路径不能是符号链接")
    destination = destination.resolve(strict=True)
    if not destination.is_dir():
        raise NotADirectoryError(f"新数据库路径不是文件夹: {destination}")
    if destination == source_root:
        info = DatabaseService.open_database(
            database_path,
            storage_root=source_root,
            expected_storage_id=None,
        )
        return info, source_root
    try:
        destination.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise ValueError("新数据库路径不能位于当前数据库路径内")
    try:
        source_root.relative_to(destination)
    except ValueError:
        pass
    else:
        raise ValueError("新数据库路径不能是当前数据库的上级目录")
    if any(destination.iterdir()):
        raise FileExistsError("新数据库路径必须是空文件夹")
    if _database_has_files(database_path):
        raise HTTPException(
            status_code=409,
            detail="当前数据库已有文件记录，快速模式下不允许迁移路径",
        )
    database_files = [
        candidate
        for candidate in database_path.parent.glob("*.sqlite3")
        if not candidate.name.startswith("._") and not candidate.is_symlink()
    ]
    if len(database_files) != 1 or database_files[0].resolve() != database_path.resolve():
        raise HTTPException(
            status_code=409,
            detail="当前路径不是独立的单数据库根目录，不能整体迁移",
        )
    for candidate in source_root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"数据库根目录包含符号链接，无法安全迁移: {candidate}")

    source_relative_database_path = database_path.resolve().relative_to(source_root)
    relative_database_path = Path("databases") / database_path.name
    token = uuid4().hex[:10]
    stage = destination.parent / f".{destination.name}.ai3-stage-{token}"
    backup = source_root.parent / f".{source_root.name}.ai3-backup-{token}"
    if stage.exists() or backup.exists():
        raise FileExistsError("数据库迁移临时路径已存在，请重试")

    try:
        shutil.copytree(source_root, stage)
        staged_database = stage / source_relative_database_path
        final_staged_database = stage / relative_database_path
        if staged_database != final_staged_database:
            final_staged_database.parent.mkdir(parents=True, exist_ok=True)
            staged_database.replace(final_staged_database)
            legacy_management = stage / "_ai3"
            if legacy_management.is_dir():
                shutil.rmtree(legacy_management)
            staged_database = final_staged_database
        # Copying a live SQLite file byte-for-byte can miss a concurrent WAL
        # checkpoint.  Replace the copied database with SQLite's own
        # consistent backup before validating and switching directories.
        staged_backup = staged_database.with_name(f".{staged_database.name}.backup")
        try:
            with DatabaseService(database_path).connect(
                readonly=True
            ) as source_connection:
                target_connection = sqlite3.connect(staged_backup)
                try:
                    source_connection.backup(target_connection)
                finally:
                    target_connection.close()
            os.replace(staged_backup, staged_database)
            for suffix in ("-wal", "-shm"):
                auxiliary = Path(f"{staged_database}{suffix}")
                if auxiliary.exists():
                    auxiliary.unlink()
        finally:
            if staged_backup.exists():
                staged_backup.unlink()
        original_info = DatabaseService.open_database(
            database_path,
            storage_root=source_root,
            expected_storage_id=None,
        )
        copied_info = DatabaseService.open_database(
            staged_database,
            storage_root=stage,
            expected_storage_id=None,
        )
        if copied_info.database_uid != original_info.database_uid:
            raise RuntimeError("数据库迁移校验失败")
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise

    destination.rmdir()
    try:
        source_root.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            backup.rename(source_root)
            destination.mkdir(parents=False, exist_ok=True)
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    final_database = destination / relative_database_path
    try:
        final_info = DatabaseService.open_database(
            final_database,
            storage_root=destination,
            expected_storage_id=None,
        )
    except BaseException:
        # The staged copy was validated before the switch, but retain an
        # explicit rollback if the final path cannot be reopened.
        failed = destination.parent / f".{destination.name}.ai3-failed-{token}"
        destination.rename(failed)
        backup.rename(source_root)
        destination.mkdir(parents=False, exist_ok=False)
        shutil.rmtree(failed)
        raise
    state.replace_database_root(source_root, destination)
    state.activate_database(
        final_info.path, database_root=destination, storage_id=final_info.default_storage_id
    )
    return final_info, backup


def _safe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (FileExistsError, sqlite3.IntegrityError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return HTTPException(status_code=422, detail=str(exc).strip("'"))
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "ai3",
        "storage_selected": state.storage_root(_active_storage_id()) is not None,
        "database_open": state.current_database is not None,
    }


@app.get("/api/status")
def status() -> dict[str, Any]:
    database: dict[str, Any] | None = None
    pending_uid = state.current_pending_database_uid
    if pending_uid is not None:
        pending = state.pending_database(pending_uid)
        if pending is not None:
            database = _pending_database_payload(pending, active=True)
    elif state.current_database is not None:
        try:
            info = DatabaseService.open_database(
                state.current_database,
                expected_storage_id=_active_storage_id(),
                require_storage=False,
            )
            database = _database_payload(info, active=True)
        except Exception as exc:
            database = {"path": str(state.current_database), "error": str(exc)}
    profiles: list[dict[str, str]] = []
    if state.profiles_dir.is_dir():
        for path in sorted(state.profiles_dir.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.lower() in {".yaml", ".yml", ".json"}:
                profiles.append({"id": path.stem, "name": path.stem})
    return {
        "service": "AI-3.0",
        "phase": 1,
        "active_database": database,
        "sample_profiles": profiles,
        "tdms_lines": sorted(load_line_rules()),
        "storage_id": _active_storage_id(),
        "storage_selected": state.storage_root(_active_storage_id()) is not None,
    }


@app.get("/api/storage")
def storage_status() -> dict[str, Any]:
    storage_id = _active_storage_id()
    selected_root = state.storage_root(storage_id)
    if selected_root is None:
        return {
            "storage_id": storage_id,
            "root_path": None,
            "selected": False,
            "mounted": False,
            "message": "当前数据库尚未配置数据文件夹",
        }
    root = _storage_root()
    try:
        _roots().root(storage_id, require_available=True)
        mounted = os.access(root, os.R_OK | os.X_OK)
    except Exception:
        mounted = False
    payload: dict[str, Any] = {
        "storage_id": storage_id,
        "root_path": str(root),
        "database_root": str(root),
        "sqlite_path": (
            str(state.current_database) if state.current_database is not None else None
        ),
        "selected": True,
        "mounted": mounted,
        "message": (
            "存储根目录可访问"
            if mounted
            else "存储根目录当前不可访问；目录更新不会批量标记缺失"
        ),
    }
    if mounted:
        usage = shutil.disk_usage(root)
        payload.update(
            {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            }
        )
    return payload


@app.post("/api/storage/select-folder")
@_serialized_database_operation
def select_storage_folder(
    request: StorageFolderSelectRequest | None = None,
) -> dict[str, Any]:
    """Create a pending database in, or move an empty database to, a folder.

    A database root contains raw files and ``databases/*.sqlite3``.
    Non-empty databases are deliberately rejected in the quick implementation
    so this endpoint never silently rebinds existing relative paths.
    """

    try:
        pending_uid = state.current_pending_database_uid
        pending = state.pending_database(pending_uid) if pending_uid else None
        active_database = None if pending is not None else _require_database()
        requested_path = request.path if request is not None else None
        chosen = (
            Path(requested_path).expanduser()
            if requested_path is not None
            else _choose_folder_macos(
                "选择数据库路径"
                if pending is not None
                else "选择当前数据库的新路径（空文件夹）"
            )
        )
        if chosen is None:
            current_storage_id = _active_storage_id()
            current = state.storage_root(current_storage_id)
            return {
                "storage_id": current_storage_id,
                "root_path": str(current) if current is not None else None,
                "selected": current is not None,
                "mounted": current is not None,
                "cancelled": True,
            }
        if pending is not None:
            selected_root = _normalize_selected_root(chosen)
            info = DatabaseService.create_database(
                database_name=pending["database_name"],
                storage_root=selected_root,
                storage_id=LEGACY_STORAGE_ID,
                database_uid=pending["database_uid"],
            )
            state.remove_pending_database(pending["database_uid"])
            state.register_database(
                info.database_uid,
                info.database_name,
                info.path,
                storage_id=info.default_storage_id,
                dataset_root=selected_root,
            )
            state.activate_database(
                info.path,
                database_root=selected_root,
                storage_id=info.default_storage_id,
            )
            return {
                "storage_id": info.default_storage_id,
                "root_path": str(selected_root),
                "database_root": str(selected_root),
                "sqlite_path": str(info.path),
                "database": _database_payload(info, active=True),
                "previous_root_backup": None,
                "selected": True,
                "mounted": True,
                "cancelled": False,
            }
        assert active_database is not None
        info, backup = _migrate_empty_database_root(active_database, chosen)
        selected = _database_root_from_path(info.path)
        state.register_database(
            info.database_uid,
            info.database_name,
            info.path,
            storage_id=info.default_storage_id,
            dataset_root=selected,
        )
        return {
            "storage_id": info.default_storage_id,
            "root_path": str(selected),
            "database_root": str(selected),
            "sqlite_path": str(info.path),
            "database": _database_payload(info, active=True),
            "previous_root_backup": (
                str(backup) if backup.resolve(strict=False) != selected else None
            ),
            "selected": True,
            "mounted": True,
            "cancelled": False,
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/select-folder")
def select_database_folder(
    request: DatabaseFolderSelectRequest | None = None,
) -> dict[str, Any]:
    """Open the single database contained in the selected database folder."""

    try:
        requested_path = request.path if request is not None else None
        chosen = (
            Path(requested_path).expanduser()
            if requested_path is not None
            else _choose_folder_macos("选择数据库名称文件夹")
        )
        if chosen is None:
            return {"selected": False, "cancelled": True, "databases": []}
        root = _normalize_selected_root(chosen)
        records = [item for item in _database_records_for_root(root) if item.get("path") and not item.get("error")]
        if len(records) != 1:
            raise ValueError("所选文件夹必须恰好包含一个 AI-3.0 数据库")
        record = records[0]
        info = DatabaseService.open_database(record["path"], storage_root=root, require_storage=False)
        state.add_database_root(root)
        state.register_database(info.database_uid, info.database_name, info.path,
                                storage_id=info.default_storage_id, dataset_root=root)
        _sync_database_lines(info.database_uid, root)
        state.activate_database(info.path, database_root=root, storage_id=info.default_storage_id)
        return {
            "selected": True,
            "cancelled": False,
            "catalog_root": str(root),
            "databases": [_database_payload(info, active=True)],
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/choose-parent-folder")
def choose_database_parent_folder(
    request: DatabaseFolderSelectRequest | None = None,
) -> dict[str, Any]:
    """Select an existing parent directory without changing the catalog."""
    try:
        chosen = (
            Path(request.path).expanduser()
            if request is not None and request.path is not None
            else _choose_folder_macos("选择新数据库的父文件夹")
        )
        if chosen is None:
            return {"cancelled": True}
        return {"cancelled": False, "path": str(_normalize_selected_root(chosen))}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/databases")
def list_databases() -> dict[str, Any]:
    return {
        "databases": _database_records(),
        "catalog_roots": [str(root) for root in state.catalog_roots()],
    }


@app.post("/api/databases", status_code=201)
@_serialized_database_operation
def create_database(request: DatabaseCreateRequest) -> dict[str, Any]:
    try:
        name = DatabaseService.database_directory_name(request.name)
        if any(
            str(record.get("database_name") or "").casefold() == name.casefold()
            for record in _database_records()
        ):
            raise FileExistsError(f"database name already exists: {name}")
        record = state.create_pending_database(str(uuid4()), name)
        return _pending_database_payload(record, active=True)
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/create-from-folder", status_code=201)
@_serialized_database_operation
def create_database_from_folder(
    request: DatabaseCreateFromFolderRequest,
) -> dict[str, Any]:
    """Create a named database folder below the selected parent folder."""

    try:
        parent = _normalize_selected_root(request.path)
        name = DatabaseService.database_directory_name(request.name)
        root = parent / name
        if root.exists() or root.is_symlink():
            raise FileExistsError(f"数据库文件夹已存在: {root}")
        storage_id = name
        registered = state.list_registered_databases()
        for record in registered:
            existing_name = str(record.get("database_name") or "")
            existing_storage_id = str(record.get("storage_id") or LEGACY_STORAGE_ID)
            existing_root = Path(
                record.get("dataset_root") or _database_root_from_path(record["path"])
            ).expanduser().resolve(strict=False)
            if existing_root == root:
                raise FileExistsError("该数据集文件夹已经存在于 catalog_state.json")
            if existing_name.casefold() == name.casefold():
                raise FileExistsError(f"数据集名称已存在: {name}")
            if existing_storage_id.casefold() == storage_id.casefold():
                raise FileExistsError(f"storage_id 已存在: {storage_id}")

        info = DatabaseService.create_in_parent(parent, name, storage_id=storage_id)
        state.add_database_root(root)
        state.register_database(
            info.database_uid,
            info.database_name,
            info.path,
            storage_id=storage_id,
            dataset_root=root,
        )
        state.activate_database(info.path, database_root=root, storage_id=storage_id)
        return {"selected": True, "cancelled": False, "database": _database_payload(info, active=True)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/open")
@_serialized_database_operation
def open_database(request: DatabaseOpenRequest) -> dict[str, Any]:
    try:
        requested = Path(request.path).expanduser().resolve(strict=False)
        saved = next(
            (
                item
                for item in state.list_registered_databases()
                if Path(item["path"]).expanduser().resolve(strict=False) == requested
            ),
            None,
        )
        if saved is None:
            raise FileNotFoundError(
                f"database path was not found in catalog_state.json: {requested}"
            )
        root, target = _catalog_root_for_database(request.path)
        info = DatabaseService.open_database(
            target,
            storage_root=root,
            expected_storage_id=str(saved.get("storage_id") or LEGACY_STORAGE_ID),
            require_storage=False,
        )
        state.activate_database(
            info.path, database_root=root, storage_id=info.default_storage_id
        )
        state.register_database(
            info.database_uid,
            info.database_name,
            info.path,
            storage_id=info.default_storage_id,
            dataset_root=root,
        )
        _sync_database_lines(info.database_uid, root)
        return _database_payload(info, active=True)
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/{database_uid}/activate")
@_serialized_database_operation
def activate_database(database_uid: str) -> dict[str, Any]:
    try:
        pending = state.pending_database(database_uid)
        if pending is not None:
            state.activate_pending_database(database_uid)
            return _pending_database_payload(pending, active=True)
        selected = _resolve_database_uid(database_uid)
        root = selected.storage_root or _database_root_from_path(selected.path)
        state.activate_database(
            selected.path,
            database_root=root,
            storage_id=selected.default_storage_id,
        )
        return _database_payload(selected, active=True)
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.patch("/api/databases/{database_uid}")
@_serialized_database_operation
def update_database(database_uid: str, request: DatabaseUpdateRequest) -> dict[str, Any]:
    try:
        pending = state.pending_database(database_uid)
        if pending is not None:
            name = DatabaseService.database_directory_name(request.name)
            updated_pending = state.update_pending_database(database_uid, name)
            return _pending_database_payload(
                updated_pending,
                active=state.current_pending_database_uid == database_uid,
            )
        selected = _resolve_database_uid(database_uid)
        original_path = selected.path
        updated = DatabaseService(original_path).update_database_name(
            request.name,
            expected_database_uid=selected.database_uid,
        )
        if updated.path != original_path:
            raise RuntimeError("database path changed while updating metadata")
        record = _catalog_record(database_uid) or {}
        state.register_database(
            updated.database_uid,
            updated.database_name,
            updated.path,
            storage_id=updated.default_storage_id,
            dataset_root=record.get("dataset_root") or updated.storage_root,
        )
        return _database_payload(
            updated,
            active=(
                state.current_database is not None
                and original_path.resolve() == state.current_database.resolve()
            ),
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.delete("/api/databases/{database_uid}")
@_serialized_database_operation
def remove_database_record(database_uid: str) -> dict[str, Any]:
    """Remove a home-page record without deleting SQLite or raw files."""

    try:
        pending = state.remove_pending_database(database_uid)
        if pending is not None:
            return {
                "database_uid": database_uid,
                "removed": True,
                "sqlite_deleted": False,
                "raw_files_deleted": False,
            }
        removed = state.unregister_database(database_uid)
        if removed is None:
            raise FileNotFoundError(
                f"database_uid was not found in catalog_state.json: {database_uid}"
            )
        return {
            "database_uid": database_uid,
            "removed": True,
            "path": removed["path"],
            "sqlite_deleted": False,
            "raw_files_deleted": False,
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/{database_uid}/lines", status_code=201)
@_serialized_database_operation
def create_database_line(
    database_uid: str, request: DatabaseLineCreateRequest
) -> dict[str, Any]:
    try:
        selected = _resolve_database_uid(database_uid)
        line = _normalize_line_name(request.name)
        root = _database_root_from_path(selected.path).resolve(strict=True)
        line_path = root / line
        if line_path.exists() and not line_path.is_dir():
            raise FileExistsError(f"同名路径不是文件夹: {line}")
        line_path.mkdir(exist_ok=True)
        lines = state.add_database_line(database_uid, line)
        DatabaseService.write_lines(root, lines)
        return {"line": line, "lines": lines}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/databases/{database_uid}/lines/auto-detect")
@_serialized_database_operation
def auto_detect_database_lines(database_uid: str) -> dict[str, Any]:
    """Register first-level dataset folders as production lines."""

    try:
        selected = _resolve_database_uid(database_uid)
        record = _catalog_record(database_uid) or {}
        root = Path(
            record.get("dataset_root") or selected.storage_root or _database_root_from_path(selected.path)
        ).expanduser().resolve(strict=True)
        reserved = {"databases", "backups", "exports", "logs", "_ai3"}
        detected: list[str] = []
        for child in root.iterdir():
            if (
                child.name.startswith(".")
                or child.name.casefold() in reserved
                or child.is_symlink()
                or not child.is_dir()
            ):
                continue
            try:
                detected.append(_normalize_line_name(child.name))
            except ValueError:
                continue
        detected = sorted(set(detected), key=str.casefold)
        existing = list(record.get("lines", []))
        existing_keys = {line.casefold() for line in existing}
        added = [line for line in detected if line.casefold() not in existing_keys]
        lines = existing
        for line in added:
            lines = state.add_database_line(database_uid, line)
        DatabaseService.write_lines(root, lines)
        return {"detected": detected, "added": added, "lines": lines}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/summary")
def summary(database_uid: str | None = None) -> dict[str, Any]:
    if database_uid is None and state.current_database is None:
        return {
            "database_open": False,
            "file_count": 0,
            "sample_count": 0,
            "import_run_count": 0,
            "issue_count": 0,
        }
    try:
        service = DatabaseService(_database_path(database_uid))
        with service.connect(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM files WHERE record_status = 'active') AS file_count,
                    (SELECT COUNT(*) FROM samples s JOIN files f ON f.file_uid = s.file_uid
                        WHERE f.record_status = 'active') AS sample_count,
                    (SELECT COUNT(*) FROM import_runs) AS import_run_count,
                    (SELECT COUNT(*) FROM files WHERE record_status = 'active' AND
                        (availability_status <> 'present' OR integrity_status <> 'verified')) AS issue_count,
                    (SELECT COUNT(*) FROM files WHERE record_status = 'active' AND availability_status = 'missing') AS missing_count,
                    (SELECT COUNT(*) FROM files WHERE record_status = 'active' AND integrity_status = 'changed') AS changed_count,
                    (SELECT COUNT(*) FROM files WHERE record_status = 'active' AND integrity_status = 'unreadable') AS unreadable_count
                """
            ).fetchone()
            return {"database_open": True, **dict(row)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/import/select-sources")
def select_import_sources(
    mode: str = Query(default="files", pattern="^(files|folder)$"),
    file_type: str = Query(default="tdms", pattern="^(tdms|wav)$"),
) -> dict[str, Any]:
    """Open the local native picker and return absolute source paths."""

    try:
        _require_database()
        if mode == "folder":
            selected = _choose_folder_macos("选择要导入的原始数据文件夹")
            paths = [] if selected is None else [selected]
        else:
            display_type = {"tdms": "TDMS / TDMS.ZST", "wav": "WAV"}[file_type]
            chosen_files = _choose_files_macos(f"选择要导入的 {display_type} 文件")
            paths = [] if chosen_files is None else chosen_files
        if not paths:
            return {"mode": mode, "paths": [], "cancelled": True}
        resolved: list[str] = []
        for path in paths:
            candidate = path.expanduser().resolve(strict=True)
            if mode == "folder" and not candidate.is_dir():
                raise NotADirectoryError(candidate)
            if mode == "files" and not candidate.is_file():
                raise FileNotFoundError(candidate)
            resolved.append(str(candidate))
        return {"mode": mode, "paths": resolved, "cancelled": False}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/import")
@_serialized_database_operation
def import_files(request: ImportRequest) -> dict[str, Any]:
    try:
        if not request.preview_id:
            raise ValueError("请先生成导入预览，再确认登记")
        stored_preview = state.get_preview(request.preview_id)
        if stored_preview.get("preview_type") != "import":
            raise ValueError("预览类型不匹配，请重新生成导入预览")
        active_database = _require_database()
        if Path(str(stored_preview.get("database_path", ""))).resolve() != active_database:
            raise ValueError("已切换数据库，请重新生成导入预览")
        canonical_request = request.model_dump(exclude={"preview_id"})
        if canonical_request != stored_preview.get("request"):
            raise ValueError("导入信息已变化，请重新生成预览")
        batch = _maintenance_service().apply_import(
            known_lines=_active_import_lines(),
            preview=stored_preview["result"],
            **_import_options(request),
        )
        state.discard_preview(request.preview_id)
        return {"result": {**batch["run"], "items": batch["items"], "files": batch["files"]}}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/import/preview")
def import_preview(request: ImportRequest) -> dict[str, Any]:
    try:
        if request.preview_id is not None:
            raise ValueError("生成预览时不得提供 preview_id")
        active_database = _require_database()
        result = _maintenance_service().preview_import(
            known_lines=_active_import_lines(), **_import_options(request),
        )
        preview_id = str(uuid4())
        state.put_preview(
            {
                "preview_id": preview_id,
                "preview_type": "import",
                "database_path": str(active_database),
                "request": request.model_dump(exclude={"preview_id"}),
                "result": result,
            }
        )
        return {
            "preview_id": preview_id,
            "result": {"items": _business_import_preview(result, request)},
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/import-items/{item_uuid}/retry-cleanup")
@_serialized_database_operation
def retry_import_cleanup(item_uuid: str) -> dict[str, Any]:
    try:
        service = ImportService(
            _require_database(),
            storage_roots=_roots(),
            profiles_dir=state.profiles_dir,
        )
        return {"item": service.retry_cleanup(item_uuid)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/files")
def list_files(
    line: list[str] = Query(default=[]),
    device_id: list[str] = Query(default=[]),
    model_name: list[str] = Query(default=[]),
    reference: list[str] = Query(default=[]),
    load_value: list[float] = Query(default=[]),
    load_unit: list[str] = Query(default=[]),
    speed_ratio: list[float] = Query(default=[]),
    acquired_at: list[str] = Query(default=[]),
    filename: str | None = Query(default=None, max_length=500),
    load_min: float | None = None,
    load_max: float | None = None,
    speed_min: float | None = None,
    speed_max: float | None = None,
    availability_status: str | None = None,
    integrity_status: str | None = None,
    limit: int = Query(default=500, ge=1, le=5_000),
    offset: int = Query(default=0, ge=0),
    database_uid: str | None = None,
) -> dict[str, Any]:
    if database_uid is None and state.current_database is None:
        return {"file_count": 0, "sample_count": 0, "files": []}
    conditions = {
        key: values
        for key, values in {
            "line": line,
            "device_id": device_id,
            "model_name": model_name,
            "reference": reference,
            "load_value": load_value,
            "load_unit": load_unit,
            "speed_ratio": speed_ratio,
            "acquired_at": acquired_at,
        }.items()
        if values
    }
    try:
        service = DatabaseService(_database_path(database_uid))
        with service.connect(readonly=True) as connection:
            result = ConditionService(connection).filter_files(
                conditions,
                filename=filename,
                load_min=load_min,
                load_max=load_max,
                speed_min=speed_min,
                speed_max=speed_max,
                availability_status=availability_status,
                integrity_status=integrity_status,
                limit=limit,
                offset=offset,
            )
            result["files"] = [
                _business_file_payload(connection, file_record)
                for file_record in result["files"]
            ]
            return result
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/files/path-options")
def file_path_options(
    database_uid: str | None = None,
    file_query: str | None = None,
    file_limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """Return registered active paths without walking the physical data root.

    Folder options are derived from every active database-relative path.  File
    results may be narrowed by a literal, case-insensitive substring and are
    capped independently so a large database cannot create an oversized UI
    response.
    """

    if database_uid is None and state.current_database is None:
        return {
            "database_uid": None,
            "files": [],
            "folders": [],
            "matched_file_count": 0,
            "files_truncated": False,
            "physical_scan_available": False,
        }
    try:
        if isinstance(file_limit, bool) or not isinstance(file_limit, int):
            raise TypeError("file_limit must be an integer")
        if not 1 <= file_limit <= 200:
            raise ValueError("file_limit must be between 1 and 200")

        database_path = _database_path(database_uid)
        with DatabaseService(database_path).connect(readonly=True) as connection:
            database_row = connection.execute(
                "SELECT database_uid FROM database_meta LIMIT 1"
            ).fetchone()
            if database_row is None:
                raise ValueError("database metadata is missing")

            rows = connection.execute(
                """
                SELECT relative_path
                FROM files
                WHERE record_status = 'active' AND storage_id = ?
                ORDER BY relative_path COLLATE AI3_PATH, relative_path
                """,
                (_resolve_database_uid(database_uid).default_storage_id if database_uid else _active_storage_id(),),
            )

            needle = file_query.casefold() if file_query else None
            files: list[str] = []
            matched_file_count = 0
            folder_paths: set[str] = set()
            for row in rows:
                relative_path = str(row["relative_path"])
                parent_parts = relative_path.split("/")[:-1]
                for depth in range(1, len(parent_parts) + 1):
                    folder_paths.add("/".join(parent_parts[:depth]))

                if needle is not None and needle not in relative_path.casefold():
                    continue
                matched_file_count += 1
                if len(files) < file_limit:
                    files.append(relative_path)

        stable_key = lambda value: (value.casefold(), value)
        return {
            "database_uid": str(database_row["database_uid"]),
            "files": files,
            "folders": sorted(folder_paths, key=stable_key),
            "matched_file_count": matched_file_count,
            "files_truncated": matched_file_count > len(files),
            "physical_scan_available": False,
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/files/condition-options")
def file_condition_options(database_uid: str | None = None) -> dict[str, Any]:
    """Return reusable condition-filter choices for one database.

    Only active file records contribute options.  Every supported field is
    present in the response so an empty database can render the same filter
    card without client-side special cases.
    """

    empty_options = {field: [] for field in CONDITION_OPTION_FIELDS}
    if database_uid is None and state.current_database is None:
        return {"database_uid": None, "options": empty_options}
    try:
        database_path = _database_path(database_uid)
        with DatabaseService(database_path).connect(readonly=True) as connection:
            database_row = connection.execute(
                "SELECT database_uid FROM database_meta LIMIT 1"
            ).fetchone()
            if database_row is None:
                raise ValueError("database metadata is missing")
            conditions = ConditionService(connection)
            options = {
                field: [
                    value
                    for value in conditions.distinct_values(field)
                    if not isinstance(value, str) or value.strip()
                ]
                for field in CONDITION_OPTION_FIELDS
            }
        return {
            "database_uid": str(database_row["database_uid"]),
            "options": options,
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/files/{file_uid}")
def file_detail(file_uid: str) -> dict[str, Any]:
    service = DatabaseService(_require_database())
    try:
        with service.connect(readonly=True) as connection:
            record = FileRepository(connection).get(file_uid)
            if record is None:
                raise HTTPException(status_code=404, detail="文件记录不存在")
            return _business_file_payload(connection, record)
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


def _label_file_samples(source: Path, line: str = "", wav_profile: str | None = None) -> list[dict[str, Any]]:
    if source.name.lower().endswith(".wav"):
        info = describe_wav(source, wav_profile)
        sample_rate = int(info["sampling_rate_hz"])
        duration = float(info["frames"] / sample_rate) if sample_rate else 0.0
        channel_mappings = info["channel_mappings"]
        return [{
            "sample_id": mapping["sample_id"],
            "display_name": mapping["display_name"],
            "sampling_rate_hz": float(sample_rate),
            "duration_s": duration,
            "missing": mapping["index"] >= int(info["channels"]),
            "locator": {"channel_index": mapping["index"], "profile": info["profile"] if info["profile"] != "generic" else None, "direction": info["direction"]},
        } for mapping in channel_mappings]
    if not line.strip():
        raise ValueError("读取 TDMS 前必须选择产线")
    discovery = discover_tdms_samples(source, line=line)
    return [{
        "sample_id": item.sample_id,
        "display_name": item.display_name,
        "sampling_rate_hz": item.sampling_rate_hz,
        "duration_s": item.duration_s,
        "locator": dict(item.locator_json),
    } for item in discovery.samples]


def _read_label_signal(source: Path, sample: Mapping[str, Any]) -> np.ndarray:
    _, values = read_signal(source, sample["locator"])
    return values


def _label_session_relative(source: Path, session_path: Path) -> str:
    try:
        return source.relative_to(session_path.parent).as_posix()
    except ValueError:
        return source.name


def _label_projects_path() -> Path:
    return USER_DATA_ROOT / "label_projects.json"


def _read_label_projects() -> list[dict[str, Any]]:
    payload = _read_standalone_labels(_label_projects_path())
    projects = payload.get("projects", [])
    if not isinstance(projects, list):
        raise ValueError("标注项目列表格式无效")
    return projects


@app.get("/api/labeling/projects")
def list_label_projects() -> dict[str, Any]:
    try:
        return {"projects": _read_label_projects()}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/projects/select-root")
def select_label_project_root() -> dict[str, Any]:
    try:
        root = _choose_folder_macos("选择标注项目根文件夹")
        return {"path": str(root.resolve(strict=True)) if root else None, "cancelled": root is None}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/projects")
@_serialized_database_operation
def save_label_project(request: LabelProjectSaveRequest) -> dict[str, Any]:
    try:
        root = Path(request.root_path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError("项目根路径必须是文件夹")
        if request.file_format == "wav" and request.file_type == "generic":
            raise ValueError("WAV 项目请选择电机或滑轨类型")
        projects = _read_label_projects()
        if any(item.get("name", "").casefold() == request.name.casefold() and item.get("project_id") != request.project_id for item in projects):
            raise ValueError("同名标注项目已存在")
        current = next((item for item in projects if item.get("project_id") == request.project_id), None) if request.project_id else None
        if request.project_id and current is None:
            raise FileNotFoundError("标注项目不存在")
        if not request.taxonomy_path:
            _load_label_taxonomy(root)
        project = {
            "project_id": current["project_id"] if current else str(uuid4()),
            "name": request.name,
            "root_path": str(root),
            "file_format": request.file_format,
            "file_type": request.file_type,
            "taxonomy_path": request.taxonomy_path,
            "history_path": request.history_path,
        }
        if current is None:
            projects.append(project)
        else:
            projects[projects.index(current)] = project
        path = _label_projects_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_standalone_labels(path, {"projects": projects})
        return project
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.delete("/api/labeling/projects/{project_id}")
@_serialized_database_operation
def delete_label_project(project_id: str) -> dict[str, Any]:
    try:
        projects = _read_label_projects()
        remaining = [item for item in projects if item.get("project_id") != project_id]
        if len(remaining) == len(projects):
            raise FileNotFoundError("标注项目不存在")
        _write_standalone_labels(_label_projects_path(), {"projects": remaining})
        return {"deleted": True, "project_id": project_id}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/projects/{project_id}/scan")
def scan_label_project(project_id: str) -> dict[str, Any]:
    try:
        project = next((item for item in _read_label_projects() if item.get("project_id") == project_id), None)
        if project is None:
            raise FileNotFoundError("标注项目不存在")
        root = Path(project["root_path"]).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError("项目根路径当前不可用")
        suffixes = (".wav",) if project["file_format"] == "wav" else (".tdms", ".tdms.zst")
        paths = sorted(str(path.resolve()) for path in root.rglob("*") if path.is_file() and path.name.lower().endswith(suffixes))
        return {"root_path": str(root), "paths": paths, "count": len(paths)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/session", status_code=201)
@_serialized_database_operation
def create_labeling_session(request: LabelSessionCreateRequest) -> dict[str, Any]:
    try:
        paths = [Path(value).expanduser().resolve(strict=True) for value in request.paths]
        if request.output_directory:
            root = Path(request.output_directory).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise NotADirectoryError("标注 JSON 保存位置不是文件夹")
        else:
            common = Path(os.path.commonpath([str(path) for path in paths]))
            root = common if common.is_dir() else common.parent
        history = Path(request.history_path).expanduser().resolve(strict=False) if request.history_path else None
        if history is not None and not history.is_file():
            history = None
        if history:
            output = history
        else:
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            output = root / f"label_{root.name}_{stamp}.json"
            suffix = 1
            while output.exists():
                output = root / f"label_{root.name}_{stamp}_{suffix:02d}.json"
                suffix += 1
        if output.exists():
            existing_output = _read_standalone_labels(output)
            if not isinstance(existing_output.get("files"), list):
                if not isinstance(existing_output.get("samples"), list) or len(paths) != 1:
                    raise ValueError(f"{output.name} 不是可用于当前文件的标注 JSON")
                backup = output.with_name(f"{output.name}.{datetime.now().astimezone():%Y%m%d_%H%M%S}.bak")
                shutil.copy2(output, backup)
                existing_output["relative_path"] = _label_session_relative(paths[0], output)
                _write_standalone_labels(output, {"files": [existing_output]})
            return {"session_path": str(output), "count": len(paths)}
        if history:
            existing = _read_standalone_labels(history)
            if isinstance(existing.get("files"), list):
                seeded_files = copy.deepcopy(existing["files"])
                for document in seeded_files:
                    relative_path = document.get("relative_path")
                    if isinstance(relative_path, str):
                        original = (history.parent / relative_path).resolve()
                        document["relative_path"] = _label_session_relative(original, output)
            elif isinstance(existing.get("samples"), list):
                if len(paths) != 1:
                    raise ValueError("单文件标注 JSON 只能用于一个测量文件")
                document = copy.deepcopy(existing)
                document["relative_path"] = _label_session_relative(paths[0], output)
                seeded_files = [document]
            else:
                raise ValueError("所选 JSON 不是有效的标注会话")
            _write_standalone_labels(output, {"files": seeded_files})
            return {"session_path": str(output), "count": len(paths)}
        seeded_files: list[dict[str, Any]] = []
        for source in paths:
            existing = _read_standalone_labels(_standalone_label_path(source))
            if not existing or isinstance(existing.get("files"), list):
                continue
            document = copy.deepcopy(existing)
            document["relative_path"] = _label_session_relative(source, output)
            seeded_files.append(document)
        _write_standalone_labels(output, {"files": seeded_files})
        return {"session_path": str(output), "count": len(paths)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/taxonomy")
def get_label_taxonomy(path: str | None = None) -> dict[str, Any]:
    try:
        if not path:
            return {"path": "", **_default_label_taxonomy()}
        source = Path(path).expanduser().resolve(strict=True) if path else PROJECT_ROOT
        taxonomy_path, taxonomy = _load_label_taxonomy(source)
        return {"path": str(taxonomy_path), **taxonomy}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/select-json")
def select_labeling_json() -> dict[str, Any]:
    try:
        selected = _choose_files_macos("选择 JSON 文件")
        if not selected:
            return {"selected": False, "cancelled": True, "path": None}
        path = selected[0].expanduser().resolve(strict=True)
        if path.suffix.lower() != ".json":
            raise ValueError("请选择 JSON 文件")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("所选 JSON 必须是对象")
        if isinstance(payload.get("results"), list) and isinstance(payload.get("reasons"), list):
            _validate_label_taxonomy(payload)
            kind = "taxonomy"
        elif isinstance(payload.get("files"), list) or isinstance(payload.get("samples"), list):
            kind = "history"
        else:
            raise ValueError("所选 JSON 不是标签类别或历史标注文件")
        return {"selected": True, "cancelled": False, "path": str(path), "kind": kind}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/queue-labels")
def labeling_queue_labels(path: str, history_path: str | None = None) -> dict[str, Any]:
    """Read saved labels for a file queue without opening any audio files."""
    try:
        selected = Path(path).expanduser().resolve(strict=False)
        if not selected.is_file() and history_path:
            selected = Path(history_path).expanduser().resolve(strict=False)
        container = _read_standalone_labels(selected)
        documents = container.get("files") if isinstance(container.get("files"), list) else ([container] if container.get("samples") else [])
        files = []
        for document in documents:
            if not isinstance(document, dict) or not isinstance(document.get("relative_path"), str):
                continue
            source = (selected.parent / document["relative_path"]).resolve(strict=False)
            events = [
                {"result_key": event.get("result_key"), "result_name": event.get("result_name"),
                 "reason_key": event.get("reason_key"), "reason_name": event.get("reason_name")}
                for sample in document.get("samples", []) if isinstance(sample, dict)
                for event in sample.get("label_events", []) if isinstance(event, dict)
            ]
            files.append({"path": str(source), "events": events})
        return {"source_path": str(selected) if selected.is_file() else None, "files": files}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.put("/api/labeling/taxonomy")
def update_label_taxonomy(request: LabelTaxonomyUpdateRequest) -> dict[str, Any]:
    try:
        if not request.path:
            raise ValueError("请先选择项目根目录，再保存标签类别")
        source = Path(request.path).expanduser().resolve(strict=True) if request.path else PROJECT_ROOT
        taxonomy_path = _label_taxonomy_path(source)
        taxonomy = _validate_label_taxonomy({"results": request.results, "reasons": request.reasons})
        _write_standalone_labels(taxonomy_path, taxonomy)
        return {"path": str(taxonomy_path), **taxonomy}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/resolve")
def resolve_labeling_file(request: TdmsLabelResolveRequest) -> dict[str, Any]:
    try:
        source = Path(request.path).expanduser().resolve(strict=True)
        discovered = _label_file_samples(source, request.line, request.wav_profile)
        if not discovered:
            raise ValueError("未识别到可标注通道")
        selected_session = Path(request.session_path).expanduser().resolve() if request.session_path else None
        sidecar = selected_session if selected_session and selected_session.is_file() else _standalone_label_path(source)
        container = _read_standalone_labels(sidecar)
        relative_key = _label_session_relative(source, sidecar)
        saved = next((item for item in container.get("files", []) if item.get("relative_path") == relative_key), {}) if isinstance(container.get("files"), list) else container
        saved_samples = {
            str(item.get("sample_id")): item
            for item in saved.get("samples", [])
            if isinstance(item, dict)
        }
        samples = []
        for sample in discovered:
            channel_prefix = f'{sample["sample_id"]}_'
            channel_events = [
                event
                for item_id, item in saved_samples.items()
                if item_id == sample["sample_id"] or item_id.startswith(channel_prefix)
                for event in item.get("label_events", [])
            ]
            samples.append({
                **sample,
                "sample_scope": {"start_s": 0.0, "end_s": float(sample["duration_s"] or 0.0)},
                "label_events": channel_events,
            })
        taxonomy_source = Path(request.taxonomy_path).expanduser().resolve(strict=True) if request.taxonomy_path else source
        taxonomy_path, taxonomy = _load_label_taxonomy(taxonomy_source)
        return {
            "file_uid": saved.get("file_uid"),
            "prototype": bool(saved.get("prototype", False)),
            "relative_path": source.name,
            "absolute_path": str(source),
            "sidecar_path": str(sidecar),
            "conditions": {"line": request.line},
            "metadata": {"wav_profile": discovered[0]["locator"].get("profile"), "direction": discovered[0]["locator"].get("direction")} if source.suffix.lower() == ".wav" else {},
            "samples": samples,
            "annotations": list(saved.get("samples", [])),
            "taxonomy_path": str(taxonomy_path),
            "taxonomy": _taxonomy_maps(taxonomy),
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/select-file")
def select_labeling_file(file_type: str = Query(default="tdms", pattern="^(tdms|wav)$")) -> dict[str, Any]:
    try:
        display = "TDMS / TDMS.ZST" if file_type == "tdms" else "WAV"
        selected = _choose_files_macos(f"选择要标注的 {display} 文件")
        if not selected:
            return {"selected": False, "cancelled": True, "path": None}
        path = selected[0].expanduser().resolve(strict=True)
        suffix_ok = path.name.lower().endswith((".tdms", ".tdms.zst")) if file_type == "tdms" else path.name.lower().endswith(".wav")
        if not suffix_ok:
            raise ValueError(f"请选择 {display} 文件")
        return {"selected": True, "cancelled": False, "path": str(path)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/labeling/select-sources")
def select_labeling_sources(
    mode: str = Query(default="files", pattern="^(files|folder)$"),
    file_type: str = Query(default="tdms", pattern="^(tdms|wav)$"),
) -> dict[str, Any]:
    try:
        if mode == "folder":
            folder = _choose_folder_macos("选择包含待标注文件的文件夹")
            if folder is None:
                return {"paths": [], "cancelled": True}
            suffixes = (".tdms", ".tdms.zst") if file_type == "tdms" else (".wav",)
            paths = sorted(path.resolve() for path in folder.rglob("*") if path.is_file() and path.name.lower().endswith(suffixes))
        else:
            display = "TDMS / TDMS.ZST" if file_type == "tdms" else "WAV"
            selected = _choose_files_macos(f"选择一个或多个待标注的 {display} 文件")
            if selected is None:
                return {"paths": [], "cancelled": True}
            suffixes = (".tdms", ".tdms.zst") if file_type == "tdms" else (".wav",)
            paths = [path.resolve(strict=True) for path in selected if path.name.lower().endswith(suffixes)]
        if mode == "folder":
            root_path = str(folder.resolve())
        elif paths:
            common = Path(os.path.commonpath([str(path) for path in paths]))
            root_path = str(common if common.is_dir() else common.parent)
        else:
            root_path = ""
        return {"paths": [str(path) for path in paths], "root_path": root_path, "cancelled": False, "count": len(paths)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/waveform")
def labeling_waveform(
    path: str,
    sample_id: str,
    line: str = "",
    wav_profile: str | None = None,
    low_frequency_filter: bool = False,
) -> dict[str, Any]:
    try:
        source = Path(path).expanduser().resolve(strict=True)
        sample = next((item for item in _label_file_samples(source, line, wav_profile) if item["sample_id"] == sample_id), None)
        if sample is None or sample.get("missing"):
            raise FileNotFoundError("文件中不存在该样本")
        locator = sample["locator"]
        cache_variant = {
            "sample_id": sample_id,
            "line": line,
            "wav_profile": wav_profile or "",
            "filter": low_frequency_filter,
            "locator": locator,
            "sampling_rate_hz": sample.get("sampling_rate_hz"),
        }
        cached = waveform_cache.get(source, cache_variant)
        if cached is not None:
            return cached
        group_name = str(locator.get("group_name") or "WAV")
        channel_name = str(locator.get("channel_name") or sample["display_name"])
        raw = _read_label_signal(source, sample)
        sampling_rate = float(sample["sampling_rate_hz"] or 20000.0)
        is_wav = source.suffix.lower() == ".wav"
        cut = None if is_wav else cut_and_normalize_rms(raw, sampling_rate)
        processed = raw if is_wav else cut.values
        cut_start_s = 0.0 if is_wav else cut.start_s
        cut_end_s = raw.size / sampling_rate if is_wav else cut.end_s

        def full_values(values: np.ndarray) -> list[float]:
            return values.astype(np.float64, copy=False).tolist() if values.size else []

        filtered = highpass_filter(processed, sampling_rate) if low_frequency_filter else None
        mel = extract_mel_spectrogram(filtered if filtered is not None else processed, sampling_rate)
        result = {
            "sample_id": sample_id,
            "group_name": group_name,
            "channel_name": channel_name,
            "sampling_rate_hz": sampling_rate,
            "duration_s": raw.size / sampling_rate,
            "cut_start_s": cut_start_s,
            "cut_end_s": cut_end_s,
            "cut_duration_s": processed.size / sampling_rate,
            "values": full_values(raw),
            "cut_values": None if is_wav else full_values(processed),
            "highpass_20hz_values": full_values(filtered) if filtered is not None else None,
            "low_frequency_filter": low_frequency_filter,
            "mel_db": np.round(mel.decibels, 2).tolist(),
            "mel_times": np.round(mel.times_s, 4).tolist(),
            "mel_freqs": np.round(mel.frequencies_hz, 2).tolist(),
        }
        if not is_wav:
            pcen = extract_pcen(processed, sampling_rate)
            mfcc = extract_mfcc(processed, sampling_rate)
            spectrum_freqs, spectrum_db = extract_power_spectrum(processed, sampling_rate)
            result.update({
                "pcen": np.round(pcen, 4).tolist(),
                "mfcc": np.round(mfcc, 3).tolist(),
                "spectrum_freqs": np.round(spectrum_freqs, 2).tolist(),
                "spectrum_db": np.round(spectrum_db, 2).tolist(),
            })
        waveform_cache.put(source, cache_variant, result)
        return result
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/audio.wav")
def labeling_audio(path: str, sample_id: str, start_s: float | None = None, end_s: float | None = None, line: str = "", wav_profile: str | None = None, low_frequency_filter: bool = False) -> Response:
    try:
        source = Path(path).expanduser().resolve(strict=True)
        sample = next((item for item in _label_file_samples(source, line, wav_profile) if item["sample_id"] == sample_id), None)
        if sample is None or sample.get("missing"):
            raise FileNotFoundError("文件中不存在该样本")
        raw = _read_label_signal(source, sample)
        sample_rate = int(sample["sampling_rate_hz"] or 20000)
        normalized = cut_and_normalize_rms(raw, sample_rate, trim_seconds=0.0 if source.suffix.lower() == ".wav" else 0.5)
        origin_s = normalized.start_s
        values = highpass_filter(normalized.values, sample_rate) if low_frequency_filter else normalized.values
        if low_frequency_filter:
            values = cut_and_normalize_rms(values, sample_rate, trim_seconds=0.0).values
        first = 0
        last = values.size
        if start_s is not None or end_s is not None:
            if start_s is None or end_s is None or not np.isfinite(start_s) or not np.isfinite(end_s) or end_s <= start_s:
                raise ValueError("事件播放范围无效")
            first = max(0, min(values.size, round((start_s - origin_s) * sample_rate)))
            last = max(0, min(values.size, round((end_s - origin_s) * sample_rate)))
            if last <= first:
                raise ValueError("事件播放范围内没有音频")
            values = values[first:last]
        pcm = np.clip(values, -1.0, 1.0)
        pcm = (pcm * 32767).astype("<i2", copy=False)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm.tobytes())
        filename = f"{source.stem}_{sample_id}.wav".replace('"', "")
        return Response(output.getvalue(), media_type="audio/wav", headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Audio-Start-S": str(origin_s + first / sample_rate),
            "X-Audio-End-S": str(origin_s + last / sample_rate),
            "Cache-Control": "no-store",
        })
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/analysis-cards")
def labeling_analysis_cards() -> dict[str, Any]:
    return {"cards": [{"id": key, **value} for key, value in CARD_SPECS.items()]}


@app.post("/api/labeling/analysis")
def labeling_analysis(request: SignalAnalysisRequest) -> dict[str, Any]:
    try:
        values = np.asarray(request.data, dtype=np.float64)
        if not np.all(np.isfinite(values)) or not np.isfinite(request.sampling_rate_hz):
            raise ValueError("分析数据和采样率必须是有限数值")
        return {
            "card_id": request.card_id,
            "sample_id": request.sample_id,
            "figure": build_analysis(request.card_id, values, request.sampling_rate_hz, request.params),
        }
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.get("/api/labeling/labels/edit-capability")
def label_edit_capability() -> dict[str, bool]:
    return {"annotator_upsert": True}


@app.post("/api/labeling/labels", status_code=201)
@_serialized_database_operation
def create_label_event(request: LabelEventCreateRequest) -> dict[str, Any]:
    try:
        source_path = Path(request.path).expanduser().resolve(strict=True)
        sample = next((item for item in _label_file_samples(source_path, request.line, request.wav_profile) if item["sample_id"] == request.source_sample_id), None)
        if sample is None or sample.get("missing"):
            raise FileNotFoundError("文件中不存在该通道")
        start_s = float(request.sample_scope.get("start_s", 0.0))
        end_s = float(request.sample_scope.get("end_s", 0.0))
        duration_s = float(sample["duration_s"] or 0.0)
        if start_s < 0 or end_s <= start_s or end_s > duration_s + 1e-6:
            raise ValueError("事件时间范围无效")
        if request.scope_kind == "whole":
            sample_rate = float(sample["sampling_rate_hz"] or 20000)
            trim_points = 0 if source_path.suffix.lower() == ".wav" else max(0, round(sample_rate * 0.5))
            total_points = round(duration_s * sample_rate)
            can_trim = total_points > 2 * trim_points
            cut_start = trim_points / sample_rate if can_trim else 0.0
            cut_end = duration_s - cut_start if can_trim else duration_s
            if abs(start_s - cut_start) > 0.001 or abs(end_s - cut_end) > 0.001:
                raise ValueError("整体标注必须覆盖完整裁剪段")
        taxonomy_source = Path(request.taxonomy_path).expanduser().resolve(strict=True) if request.taxonomy_path else source_path
        _, taxonomy = _load_label_taxonomy(taxonomy_source)
        maps = _taxonomy_maps(taxonomy)
        result = maps["results"].get(request.result_key)
        if result is None:
            raise ValueError("标注结果不在标签类别中")
        reason = maps["reasons"].get(request.reason_key)
        if reason is None or reason["parent"] != request.result_key:
            raise ValueError("标注原因与总体结果不一致")
        event = {
            "event_uuid": str(uuid4()),
            "scope_kind": request.scope_kind,
            "source": request.source,
            "result_key": request.result_key,
            "result_id": result["id"],
            "result_name": result["name"],
            "result_confidence": request.result_confidence,
            "reason_key": request.reason_key,
            "reason_id": reason["id"],
            "reason_name": reason["name"],
            "reason_confidence": request.reason_confidence,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": request.note,
        }
        if not request.session_path:
            raise ValueError("请先点击“开始标注”创建标注会话")
        sidecar = Path(request.session_path).expanduser().resolve(strict=True)
        container = _read_standalone_labels(sidecar)
        if not isinstance(container.get("files"), list):
            raise ValueError("标注会话 JSON 格式无效")
        relative_key = _label_session_relative(source_path, sidecar)
        document = next((item for item in container["files"] if item.get("relative_path") == relative_key), None)
        if document is None:
            document = {
                "file_uid": str(uuid4()),
                "storage_id": sidecar.parent.name or "standalone",
                "relative_path": _label_session_relative(source_path, sidecar),
                "prototype": bool(request.prototype),
                "conditions": {},
                "metadata": {},
                "samples": [],
            }
            container["files"].append(document)
        document["prototype"] = bool(document.get("prototype")) or bool(request.prototype)
        samples = document.setdefault("samples", [])
        target = next((item for item in samples if item.get("sample_id") == request.sample_id), None)
        if target is None:
            target = {
                "sample_id": request.sample_id,
                "sample_scope": {"start_s": start_s, "end_s": end_s},
                "label_events": [],
            }
            samples.append(target)
        else:
            target["sample_scope"] = {"start_s": start_s, "end_s": end_s}
        events = target.setdefault("label_events", [])
        replaced_event_uuid = None
        if request.target_event_uuid:
            selected = next((item for item in events if item.get("event_uuid") == request.target_event_uuid), None)
            if selected is None:
                raise FileNotFoundError("要编辑或确认的标注事件不存在")
            same_source = selected if selected.get("source") == request.source else next(
                (item for item in reversed(events) if item.get("source") == request.source), None
            )
            if same_source is not None:
                replaced_event_uuid = same_source.get("event_uuid")
                event["event_uuid"] = replaced_event_uuid
                events.remove(same_source)
                events.append(event)
            else:
                events.append(event)
        else:
            events.append(event)
        _write_standalone_labels(sidecar, container)
        return {**event, "file_uid": document["file_uid"], "sample_id": request.sample_id, "sample_scope": target["sample_scope"], "prototype": bool(document.get("prototype")), "sidecar_path": str(sidecar), "replaced_event_uuid": replaced_event_uuid}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.delete("/api/labeling/labels")
@_serialized_database_operation
def delete_label_event(request: LabelEventDeleteRequest) -> dict[str, Any]:
    """Remove exactly one saved event from the selected label session."""
    try:
        source = Path(request.path).expanduser().resolve(strict=True)
        sidecar = Path(request.session_path).expanduser().resolve(strict=True)
        container = _read_standalone_labels(sidecar)
        files = container.get("files")
        if not isinstance(files, list):
            raise ValueError("标注会话 JSON 格式无效")
        relative_key = _label_session_relative(source, sidecar)
        document = next((item for item in files if item.get("relative_path") == relative_key), None)
        if document is None:
            raise FileNotFoundError("当前文件没有已保存标注")
        samples = document.get("samples")
        if not isinstance(samples, list):
            raise ValueError("标注会话中的样本列表无效")
        sample = next((item for item in samples if item.get("sample_id") == request.sample_id), None)
        if sample is None or not isinstance(sample.get("label_events"), list):
            raise FileNotFoundError("标注事件不存在")
        events = sample["label_events"]
        matches = [item for item in events if item.get("event_uuid") == request.event_uuid]
        if len(matches) != 1:
            raise FileNotFoundError("标注事件不存在或 ID 不唯一")
        sample["label_events"] = [item for item in events if item.get("event_uuid") != request.event_uuid]
        if not sample["label_events"]:
            samples.remove(sample)
        _write_standalone_labels(sidecar, container)
        return {"event_uuid": request.event_uuid, "sample_id": request.sample_id,
                "remaining_events": len(sample["label_events"]), "sidecar_path": str(sidecar)}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/export/files-by-line")
def export_files_by_line(database_uid: str) -> dict[str, Any]:
    try:
        return _maintenance_service(database_uid, require_storage=False).export_files_by_line()
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.patch("/api/files/conditions")
@_serialized_database_operation
def update_conditions(
    request: ConditionUpdateRequest,
    database_uid: str | None = None,
) -> dict[str, Any]:
    patch = request.conditions.model_dump(exclude_unset=True)
    try:
        changed = _maintenance_service(database_uid, require_storage=False).update_conditions(
            patch,
            file_uids=request.file_uids,
            scope=request.scope,
            relative_path=request.relative_path,
        )
        return {"updated_count": changed}
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/reconcile/preview")
def reconcile_preview(
    scope: str = "database",
    relative_path: str | None = None,
    database_uid: str | None = None,
) -> dict[str, Any]:
    try:
        preview = _maintenance_service(database_uid).preview_paths(
            storage_id=_resolve_database_uid(database_uid).default_storage_id if database_uid is not None else _active_storage_id(),
            scope=scope,
            relative_path=relative_path,
        )
        state.put_preview(preview)
        return preview
    except Exception as exc:
        raise _safe_error(exc) from exc


@app.post("/api/reconcile/apply")
@_serialized_database_operation
def reconcile_apply(request: ReconcileApplyRequest) -> dict[str, Any]:
    try:
        preview = state.get_preview(request.preview_id)
        return _maintenance_service(request.database_uid).apply_paths(
            preview,
            request.actions,
            replacement_conditions=(
                request.replacement_conditions.model_dump(exclude_unset=True)
                if request.replacement_conditions is not None
                else None
            ),
        )
    except Exception as exc:
        raise _safe_error(exc) from exc
