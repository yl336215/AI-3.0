from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.database import (
    ConditionService,
    DatabaseService,
    FileRepository,
    ReconcileService,
)
from tests.helpers import write_wav
from web.api.models import ConditionUpdateRequest, Conditions, ReconcileApplyRequest
from web.api import main


def _file_rows(database_info) -> list[dict[str, object]]:
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        return FileRepository(connection).list(record_status=None, limit=None)


def _metadata(record: dict[str, object]) -> dict[str, object]:
    value = record["metadata_json"]
    return json.loads(value) if isinstance(value, str) else dict(value)


def test_import_and_patch_custom_conditions_in_metadata(
    importer, database_info, external_root
):
    source = write_wav(external_root / "conditions.wav")
    result = importer.import_paths(
        [source],
        target_relative_dir="line",
        conditions={
            "line": "L1",
            "device_id": "DAQ-1",
            "model_name": "Project-A",
            "reference": "Model-A",
            "load_value": 12.5,
            "load_unit": "N",
            "speed_ratio": 1.25,
            "acquired_at": "2026-09-23T10:30:00+08:00",
            "extra_fields": {
                "operator": "Alice",
                "shift": 2,
                "ignored_null": None,
            },
        },
    )
    assert result["run"]["success_count"] == 1

    record = _file_rows(database_info)[0]
    assert record["line"] == "L1"
    assert record["device_id"] == "DAQ-1"
    assert record["model_name"] == "Project-A"
    assert record["reference"] == "Model-A"
    assert record["load_value"] == 12.5
    assert record["speed_ratio"] == 1.25
    assert record["acquired_at"] == "2026-09-23T10:30:00+08:00"
    metadata = _metadata(record)
    assert metadata["condition_extras"] == {"operator": "Alice", "shift": 2}
    assert "extra_fields" not in record

    with DatabaseService(database_info.path).connect() as connection:
        changed = ConditionService(connection).update_files(
            [str(record["file_uid"])],
            {
                "line": "L2",
                "extra_fields": {
                    "operator": "Bob",
                    "shift": None,
                    "approved": True,
                },
            },
        )
    assert changed == 1
    updated = _file_rows(database_info)[0]
    assert updated["line"] == "L2"
    updated_metadata = _metadata(updated)
    assert updated_metadata["condition_extras"] == {
        "approved": True,
        "operator": "Bob",
    }
    assert "validated_metadata" in updated_metadata

    with DatabaseService(database_info.path).connect() as connection:
        ConditionService(connection).update_files(
            [str(record["file_uid"])],
            {"extra_fields": {"operator": None, "approved": None}},
        )
    cleared = _metadata(_file_rows(database_info)[0])
    assert "condition_extras" not in cleared
    assert "validated_metadata" in cleared


def test_folder_import_is_recursive_stable_and_excludes_sidecars(
    importer, database_info, external_root, storage_root
):
    selected = external_root / "batch"
    top = write_wav(selected / "top.wav")
    child = write_wav(selected / "nested" / "child.wav")
    write_wav(selected / "_ai3" / "ignored.wav")
    write_wav(selected / "temporary" / "ignored.wav")
    write_wav(selected / "nested" / "ignored.wav.partial")
    (selected / "nested" / "state.sqlite3").write_bytes(b"not raw data")
    (selected / "linked.wav").symlink_to(top)
    (selected / "linked-folder").symlink_to(selected / "nested", target_is_directory=True)

    first = importer.preview_paths(
        [top, selected], target_relative_dir="imports"
    )
    second = importer.preview_paths(
        [selected, top], target_relative_dir="imports"
    )
    first_signature = [
        (
            item["source_display_path"],
            item["target_relative_path"],
            item["status"],
            item["payload_sha256"],
        )
        for item in first["items"]
    ]
    second_signature = [
        (
            item["source_display_path"],
            item["target_relative_path"],
            item["status"],
            item["payload_sha256"],
        )
        for item in second["items"]
    ]
    assert first_signature == second_signature
    assert first["source_scope"] == "outside"
    assert {item["target_relative_path"] for item in first["items"]} == {
        "imports/batch/top.wav",
        "imports/batch/nested/child.wav",
    }

    imported = importer.import_paths(
        [selected, top], target_relative_dir="imports"
    )
    assert imported["run"]["success_count"] == 2
    assert {row["relative_path"] for row in _file_rows(database_info)} == {
        "imports/batch/top.wav",
        "imports/batch/nested/child.wav",
    }
    assert (storage_root / "imports" / "batch" / "top.wav").is_file()
    assert (storage_root / "imports" / "batch" / "nested" / "child.wav").is_file()
    assert child.is_file()


def test_inside_folder_registration_keeps_existing_storage_paths(
    importer, database_info, storage_root
):
    source = write_wav(storage_root / "line" / "inside.wav")

    preview = importer.preview_paths([source.parent])

    assert preview["source_scope"] == "inside"
    assert [item["target_relative_path"] for item in preview["items"]] == [
        "line/inside.wav"
    ]
    result = importer.import_paths([source.parent])
    assert result["run"]["success_count"] == 1
    assert source.is_file()


def test_condition_update_target_contract():
    conditions = Conditions(line="L1")
    direct = ConditionUpdateRequest(file_uids=["file-1"], conditions=conditions)
    assert direct.file_uids == ["file-1"]
    database = ConditionUpdateRequest(scope="database", conditions=conditions)
    assert database.scope == "database"
    folder = ConditionUpdateRequest(
        scope="folder", relative_path="line/day", conditions=conditions
    )
    assert folder.relative_path == "line/day"

    with pytest.raises(ValidationError):
        ConditionUpdateRequest(conditions=conditions)
    with pytest.raises(ValidationError):
        ConditionUpdateRequest(
            file_uids=["file-1"], scope="file", relative_path="a.wav", conditions=conditions
        )
    with pytest.raises(ValidationError):
        ConditionUpdateRequest(scope="folder", conditions=conditions)
    with pytest.raises(ValidationError):
        ConditionUpdateRequest(
            scope="database", relative_path="not-allowed", conditions=conditions
        )
    with pytest.raises(ValidationError):
        Conditions(extra_fields={"line": "ambiguous"})


def test_custom_condition_total_limit_is_enforced_after_merge(
    importer, database_info, external_root
):
    source = write_wav(external_root / "many-extra-fields.wav")
    result = importer.import_paths(
        [source],
        target_relative_dir="line",
        conditions={
            "extra_fields": {f"field_{index}": index for index in range(32)}
        },
    )
    file_uid = str(result["files"][0]["file_uid"])

    with DatabaseService(database_info.path).connect() as connection:
        with pytest.raises(ValueError, match="at most 32"):
            ConditionService(connection).update_files(
                [file_uid], {"extra_fields": {"field_32": 32}}
            )


def test_reconcile_replacement_stores_custom_conditions(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "replace-extra.wav", sample_rate=8_000)
    importer.import_paths([source], target_relative_dir="line")
    old = _file_rows(database_info)[0]
    write_wav(storage_root / str(old["relative_path"]), sample_rate=16_000)

    service = ReconcileService(
        database_info.path,
        storage_roots=storage_roots,
        profiles_dir=Path(__file__).resolve().parents[1] / "config" / "sample_profiles",
    )
    preview = service.preview()
    replacement = next(
        item for item in preview["items"] if item["category"] == "content_replaced"
    )
    service.apply(
        preview,
        [{"item_id": replacement["item_id"], "action": "supersede_and_register"}],
        replacement_conditions={
            "line": "L-new",
            "extra_fields": {"operator": "Carol", "batch": 3},
        },
    )

    active = next(
        row for row in _file_rows(database_info) if row["record_status"] == "active"
    )
    assert active["line"] == "L-new"
    assert _metadata(active)["condition_extras"] == {
        "batch": 3,
        "operator": "Carol",
    }


def test_reconcile_path_only_inherits_conditions_and_partial_patch_keeps_omitted(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "replace-inherit.wav", sample_rate=8_000)
    importer.import_paths(
        [source],
        target_relative_dir="line",
        conditions={
            "line": "L-old",
            "device_id": "DAQ-1",
            "model_name": "Project-A",
            "reference": "Model-A",
            "load_value": 12.5,
            "load_unit": "N",
            "speed_ratio": 1.25,
            "acquired_at": "2026-09-23T10:30:00+08:00",
            "extra_fields": {"operator": "Alice", "batch": 3},
        },
    )
    service = ReconcileService(
        database_info.path,
        storage_roots=storage_roots,
        profiles_dir=Path(__file__).resolve().parents[1] / "config" / "sample_profiles",
    )

    old = next(row for row in _file_rows(database_info) if row["record_status"] == "active")
    stored = storage_root / str(old["relative_path"])
    write_wav(stored, sample_rate=16_000)
    preview = service.preview()
    replacement = next(
        item for item in preview["items"] if item["category"] == "content_replaced"
    )
    service.apply(
        preview,
        [{"item_id": replacement["item_id"], "action": "supersede_and_register"}],
        replacement_conditions=None,
    )

    inherited = next(
        row for row in _file_rows(database_info) if row["record_status"] == "active"
    )
    for field in (
        "line",
        "device_id",
        "model_name",
        "reference",
        "load_value",
        "load_unit",
        "speed_ratio",
        "acquired_at",
    ):
        assert inherited[field] == old[field]
    assert _metadata(inherited)["condition_extras"] == {
        "batch": 3,
        "operator": "Alice",
    }

    write_wav(stored, sample_rate=22_050)
    preview = service.preview()
    replacement = next(
        item for item in preview["items"] if item["category"] == "content_replaced"
    )
    service.apply(
        preview,
        [{"item_id": replacement["item_id"], "action": "supersede_and_register"}],
        replacement_conditions={
            "line": "L-new",
            "reference": None,
            "extra_fields": {"operator": None, "batch": 4},
        },
    )

    patched = next(
        row for row in _file_rows(database_info) if row["record_status"] == "active"
    )
    assert patched["line"] == "L-new"
    assert patched["reference"] is None
    for field in (
        "device_id",
        "model_name",
        "load_value",
        "load_unit",
        "speed_ratio",
        "acquired_at",
    ):
        assert patched[field] == old[field]
    assert _metadata(patched)["condition_extras"] == {"batch": 4}


def test_reconcile_request_only_dumps_explicit_replacement_conditions():
    request = ReconcileApplyRequest(
        preview_id="preview-1",
        replacement_conditions=Conditions(line="L-new"),
    )

    assert request.replacement_conditions is not None
    assert request.replacement_conditions.model_dump(exclude_unset=True) == {
        "line": "L-new"
    }


def test_condition_update_api_targets_folder_and_file(
    importer, database_info, external_root, storage_root, monkeypatch
):
    first = write_wav(external_root / "first.wav")
    second = write_wav(external_root / "second.wav")
    outside = write_wav(external_root / "outside.wav")
    importer.import_paths([first], target_relative_dir="line/day")
    importer.import_paths([second], target_relative_dir="line/day/sub")
    importer.import_paths([outside], target_relative_dir="other")
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})

    response = main.update_conditions(
        ConditionUpdateRequest(
            scope="folder",
            relative_path="line/day",
            conditions=Conditions(
                line="L-folder", extra_fields={"operator": "Alice"}
            ),
        )
    )
    assert response == {"updated_count": 2}

    rows = _file_rows(database_info)
    by_path = {str(row["relative_path"]): row for row in rows}
    assert by_path["line/day/first.wav"]["line"] == "L-folder"
    assert by_path["line/day/sub/second.wav"]["line"] == "L-folder"
    assert by_path["other/outside.wav"]["line"] is None
    assert _metadata(by_path["line/day/first.wav"])["condition_extras"] == {
        "operator": "Alice"
    }

    response = main.update_conditions(
        ConditionUpdateRequest(
            scope="file",
            relative_path="line/day/first.wav",
            conditions=Conditions(model_name="M-1"),
        )
    )
    assert response == {"updated_count": 1}
    by_path = {
        str(row["relative_path"]): row for row in _file_rows(database_info)
    }
    assert by_path["line/day/first.wav"]["model_name"] == "M-1"
    assert by_path["line/day/sub/second.wav"]["model_name"] is None
