from __future__ import annotations

from core.database import DataMaintenanceService, DatabaseService, FileRepository
from tests.helpers import write_wav
from web.api import main
from web.api.models import ConditionUpdateRequest, Conditions


def test_core_maintenance_runs_without_web_state(database_info, storage_roots, external_root):
    service = DataMaintenanceService(database_info.path, storage_roots=storage_roots)
    source = write_wav(external_root / "source.wav")
    options = {
        "source_paths": [str(source)],
        "target_storage_id": "wuxi_raw",
        "target_relative_dir": "line",
        "expected_source_scope": "outside",
        "transfer_mode": "copy",
        "expected_file_kind": "wav",
        "conditions": {"line": "line", "reference": "A"},
    }

    preview = service.preview_import(known_lines=["line"], **options)
    assert preview["items"][0]["target_relative_path"] == "line/source.wav"
    result = service.apply_import(known_lines=["line"], preview=preview, **options)
    assert result["run"]["success_count"] == 1
    file_uid = result["files"][0]["file_uid"]

    assert service.update_conditions({"reference": "B"}, scope="folder", relative_path="line") == 1
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        record = FileRepository(connection).get(file_uid)
    assert record["reference"] == "B"
    assert record["file_uid"] == preview["items"][0]["file_uid"]

    path_preview = service.preview_paths(storage_id="wuxi_raw", scope="file", relative_path="line/source.wav")
    assert path_preview["scope"] == "file"
    assert [(item["category"], item["file_uid"]) for item in path_preview["items"]] == [("unchanged", file_uid)]


def test_core_catalog_list_reports_saved_database_status(database_info):
    records = DatabaseService.list_catalog_records(
        [],
        [{
            "database_uid": database_info.database_uid,
            "database_name": database_info.database_name,
            "path": str(database_info.path),
            "storage_id": database_info.default_storage_id,
        }],
        current_database=database_info.path,
    )
    assert records[0]["active"] is True
    assert records[0]["path_exists"] is True
    assert records[0]["database_root"] == str(database_info.path.parent.parent)


def test_condition_update_does_not_require_mounted_storage(database_info, importer, external_root, monkeypatch):
    result = importer.import_paths([write_wav(external_root / "offline.wav")], target_relative_dir="line")
    file_uid = result["files"][0]["file_uid"]
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    monkeypatch.setattr(main.state, "storage_roots", {})

    response = main.update_conditions(ConditionUpdateRequest(
        file_uids=[file_uid], conditions=Conditions(reference="updated"),
    ))
    assert response == {"updated_count": 1}
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        assert FileRepository(connection).get(file_uid)["reference"] == "updated"
