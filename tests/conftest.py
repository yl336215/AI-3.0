from __future__ import annotations

from pathlib import Path

import pytest

from core.database import DatabaseService, ImportService
from core.files import StorageRoots


@pytest.fixture(autouse=True)
def isolate_runtime_catalog_state(tmp_path: Path, monkeypatch):
    """Keep API tests from writing catalog entries into the live app state."""

    from web.api import main

    monkeypatch.setattr(main.state, "catalog_state_path", tmp_path / "catalog-state.json")


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "wuxi_raw"
    root.mkdir()
    return root


@pytest.fixture
def external_root(tmp_path: Path) -> Path:
    root = tmp_path / "external"
    root.mkdir()
    return root


@pytest.fixture
def storage_roots(storage_root: Path) -> StorageRoots:
    return StorageRoots({"wuxi_raw": storage_root})


@pytest.fixture
def database_info(storage_root: Path):
    return DatabaseService.create_database(
        database_name="phase1_test", storage_root=storage_root
    )


@pytest.fixture
def importer(database_info, storage_roots: StorageRoots) -> ImportService:
    return ImportService(
        database_info.path,
        storage_roots=storage_roots,
        profiles_dir=Path(__file__).resolve().parents[1] / "config" / "sample_profiles",
    )
