"""The database folder is portable together with its line list."""

import json

from core.database import DatabaseService
from web.api import main
from web.api.models import (
    DatabaseCreateFromFolderRequest,
    DatabaseFolderSelectRequest,
    DatabaseLineCreateRequest,
)
from web.api.runtime import RuntimeState


def test_named_database_folder_can_be_reopened_with_lines(tmp_path, monkeypatch):
    parent = tmp_path / "catalog"
    parent.mkdir()
    monkeypatch.setenv("AI3_DATABASE_ROOT", str(tmp_path / "app-data"))
    monkeypatch.setenv("AI3_CATALOG_STATE", str(tmp_path / "catalog-state.json"))
    monkeypatch.setattr(main, "state", RuntimeState())

    created = main.create_database_from_folder(
        DatabaseCreateFromFolderRequest(path=str(parent), name="test-dataset")
    )["database"]
    root = parent / "test-dataset"
    assert created["path"] == str(root / "databases" / "test-dataset.sqlite3")
    assert json.loads((root / "databases" / "lines.json").read_text()) == {"lines": []}

    main.create_database_line(
        created["database_uid"], DatabaseLineCreateRequest(name="epump2")
    )
    assert json.loads((root / "databases" / "lines.json").read_text()) == {"lines": ["epump2"]}

    main.state.unregister_database(created["database_uid"])
    reopened = main.select_database_folder(DatabaseFolderSelectRequest(path=str(root)))
    assert reopened["databases"][0]["database_uid"] == created["database_uid"]
    assert main.state.list_registered_databases()[0]["lines"] == ["epump2"]


def test_old_root_lines_file_moves_to_database_directory(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    DatabaseService.create_database(database_name="dataset", storage_root=root)
    old_path = root / "lines.json"
    old_path.write_text('{"lines": ["epump2"]}\n', encoding="utf-8")

    assert DatabaseService.migrate_lines(root) == ["epump2"]
    assert not old_path.exists()
    assert json.loads((root / "databases" / "lines.json").read_text()) == {"lines": ["epump2"]}
