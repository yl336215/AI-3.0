import json

from core.database.repositories.files import FileRepository
from core.database.services.data_maintenance_service import DataMaintenanceService
from core.database.services.database_service import DatabaseService
from starlette.routing import Match
from web.api import main


def test_export_active_files_by_line(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    info = DatabaseService.create_database(database_name="dataset", storage_root=root)
    DatabaseService.write_lines(root, ["epump2", "epump3"])
    database = DatabaseService(info.path)
    with database.connect() as connection:
        FileRepository(connection).create({
            "file_uid": "file-1", "storage_id": "wuxi_raw",
            "relative_path": "epump2/20260319/E-pump_001.tdms.zst",
            "payload_sha256": "a" * 64, "stored_sha256": "b" * 64,
            "payload_size_bytes": 1, "stored_size_bytes": 1, "stored_mtime_ns": 1,
            "line": "epump2", "device_id": "daq_01", "model_name": "机型A",
            "reference": "reference_A", "load_value": 80, "load_unit": "%",
            "speed_ratio": 1.0, "acquired_at": "2026-09-22T10:30:00+08:00",
            "metadata_json": {"other": "value"},
        })
    result = DataMaintenanceService(info.path, storage_roots={"wuxi_raw": root}).export_files_by_line()
    assert result["line_count"] == 2
    assert result["file_count"] == 1
    exported = json.loads((root / "databases" / "exports" / "epump2.json").read_text())
    assert exported == [{
        "file_uid": "file-1", "storage_id": "wuxi_raw",
        "relative_path": "epump2/20260319/E-pump_001.tdms.zst",
        "conditions": {
            "line": "epump2", "device_id": "daq_01", "model_name": "机型A",
            "reference": "reference_A", "load_value": 80, "load_unit": "%",
            "speed_ratio": 1.0, "timestamp": "2026-09-22T10:30:00+08:00",
        },
        "metadata": {"other": "value"},
    }]
    assert json.loads((root / "databases" / "exports" / "epump3.json").read_text()) == []


def test_export_post_route_is_registered():
    scope = {"type": "http", "path": "/api/export/files-by-line", "method": "POST"}
    assert any(route.matches(scope)[0] == Match.FULL for route in main.app.routes)
