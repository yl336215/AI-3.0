from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core.database import (
    ConditionService,
    DatabaseService,
    FileRepository,
    SampleRepository,
)
from core.database.schema import transaction
from core.files import (
    InvalidRelativePathError,
    StorageEscapeError,
    StorageRoots,
    normalize_relative_path,
    scan_storage,
)
from core.files.publisher import (
    acquire_publication_lock,
    publish_noreplace,
    release_publication_lock,
)
import core.database.services.database_service as database_service_module
import core.files.fingerprint as fingerprint_module
from tests.helpers import write_wav


def file_values(path: str, *, payload: str, **conditions):
    return {
        "storage_id": "wuxi_raw",
        "relative_path": path,
        "payload_sha256": payload,
        "stored_sha256": payload,
        "payload_size_bytes": 10,
        "stored_size_bytes": 10,
        "stored_mtime_ns": 1,
        **conditions,
    }


def test_publish_noreplace_keeps_an_existing_formal_target(tmp_path):
    partial = tmp_path / "new.wav.partial"
    target = tmp_path / "formal.wav"
    partial.write_bytes(b"new")
    target.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        publish_noreplace(partial, target)

    assert target.read_bytes() == b"existing"
    assert partial.read_bytes() == b"new"


def test_stale_publication_lock_file_does_not_block_retry(tmp_path):
    target = tmp_path / "formal.wav"
    lock_path = tmp_path / ".formal.wav.ai3-publish.lock"
    lock_path.write_text("pid=999999\n", encoding="ascii")

    first = acquire_publication_lock(target)
    release_publication_lock(first)
    second = acquire_publication_lock(target)
    release_publication_lock(second)

    assert lock_path.is_file()


def test_publication_lock_never_writes_through_a_hardlink(tmp_path):
    target = tmp_path / "formal.wav"
    victim = tmp_path / "victim.tdms"
    victim.write_bytes(b"IMPORTANT-RAW-DATA")
    lock_path = tmp_path / ".formal.wav.ai3-publish.lock"
    os.link(victim, lock_path)

    with pytest.raises(RuntimeError, match="unsafe publication lock"):
        acquire_publication_lock(target)

    assert victim.read_bytes() == b"IMPORTANT-RAW-DATA"


def test_transaction_rolls_back_when_commit_fails(tmp_path):
    class CommitFailureConnection(sqlite3.Connection):
        fail_commit = False

        def commit(self):
            if self.fail_commit:
                self.fail_commit = False
                raise sqlite3.OperationalError("injected commit failure")
            return super().commit()

    connection = sqlite3.connect(
        tmp_path / "commit.sqlite3",
        isolation_level=None,
        factory=CommitFailureConnection,
    )
    connection.execute("CREATE TABLE values_table (value TEXT)")
    connection.fail_commit = True

    with pytest.raises(sqlite3.OperationalError):
        with transaction(connection):
            connection.execute("INSERT INTO values_table VALUES ('uncommitted')")

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
    connection.close()


def test_failed_database_creator_does_not_delete_a_replacement(
    storage_root, monkeypatch
):
    target = storage_root / "databases" / "race.sqlite3"
    original_initialize = database_service_module.initialize_schema

    def replace_and_fail(*args, **kwargs):
        target.unlink()
        target.write_bytes(b"created by another process")
        raise RuntimeError("injected initialization failure")

    monkeypatch.setattr(database_service_module, "initialize_schema", replace_and_fail)
    with pytest.raises(RuntimeError):
        DatabaseService.create_database(
            database_name="race",
            storage_root=storage_root,
        )
    monkeypatch.setattr(database_service_module, "initialize_schema", original_initialize)

    assert target.read_bytes() == b"created by another process"


def test_fingerprint_rejects_a_file_changed_during_read(tmp_path, monkeypatch):
    source = write_wav(tmp_path / "changing.wav", sample_rate=8_000)
    original_digest = fingerprint_module.stored_fingerprint

    def digest_then_change(path):
        digest = original_digest(path)
        write_wav(Path(path), sample_rate=16_000)
        return digest

    monkeypatch.setattr(fingerprint_module, "stored_fingerprint", digest_then_change)
    with pytest.raises(RuntimeError, match="changed while"):
        fingerprint_module.fingerprint_file(source, kind="wav")


def test_schema_tables_constraints_and_wal(database_info):
    management_root = database_info.path.parent
    assert {
        path.name for path in management_root.iterdir() if path.is_dir()
    } >= {"backups", "exports", "logs"}
    assert database_info.path.parent.name == "databases"
    assert not any(
        (database_info.path.parents[1] / name).exists()
        for name in ("backups", "exports", "logs")
    )
    service = DatabaseService(database_info.path)
    with service.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "database_meta",
            "files",
            "samples",
            "import_runs",
            "import_items",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        with pytest.raises(ValueError):
            FileRepository(connection).create(
                file_values("bad/raw.tdms", payload="a" * 64)
            )
        with pytest.raises(ValueError):
            FileRepository(connection).create(
                file_values("C:\\bad\\raw.wav", payload="a" * 64)
            )


def test_active_path_unique_but_superseded_path_can_be_reused(database_info):
    service = DatabaseService(database_info.path)
    with service.connect() as connection:
        files = FileRepository(connection)
        first = files.create(file_values("line/a.wav", payload="a" * 64))
        with pytest.raises(sqlite3.IntegrityError):
            files.create(file_values("line/a.wav", payload="b" * 64))
        files.supersede(first["file_uid"])
        second = files.create(file_values("line/a.wav", payload="b" * 64))
        assert first["file_uid"] != second["file_uid"]


def test_active_path_uniqueness_uses_unicode_casefold(database_info):
    service = DatabaseService(database_info.path)
    with service.connect() as connection:
        files = FileRepository(connection)
        created = files.create(file_values("line/Ä.wav", payload="a" * 64))
        assert files.get_by_path("wuxi_raw", "line/ä.wav")["file_uid"] == created["file_uid"]
        with pytest.raises(sqlite3.IntegrityError):
            files.create(file_values("line/ä.wav", payload="b" * 64))


def test_relative_paths_preserve_spaces_and_reject_symlink_components(tmp_path):
    assert normalize_relative_path(" line / file.wav ") == " line / file.wav "
    root = tmp_path / "root"
    real = root / "real"
    real.mkdir(parents=True)
    (root / "alias").symlink_to(real, target_is_directory=True)
    roots = StorageRoots({"wuxi_raw": root})

    with pytest.raises(StorageEscapeError, match="symlink"):
        roots.resolve("wuxi_raw", "alias/file.wav")


def test_condition_filter_and_or_ranges(database_info):
    service = DatabaseService(database_info.path)
    with service.connect() as connection:
        files = FileRepository(connection)
        samples = SampleRepository(connection)
        for index, (line, model, load, speed) in enumerate(
            [
                ("L1", "A", 10.0, 1.0),
                ("L2", "A", 20.0, 2.0),
                ("L1", "B", 30.0, 3.0),
            ]
        ):
            record = files.create(
                file_values(
                    f"line/{index}.wav",
                    payload=f"{index:064d}",
                    line=line,
                    model_name=model,
                    load_value=load,
                    speed_ratio=speed,
                )
            )
            samples.create(
                {
                    "file_uid": record["file_uid"],
                    "sample_id": "main",
                    "sample_scope": "whole_file",
                    "display_name": "Main",
                    "locator_json": {"channel_index": 0},
                }
            )
        result = ConditionService(connection).filter_files(
            {"line": ["L1", "L2"], "model_name": ["A"]},
            load_min=15,
            load_max=25,
        )
        assert result["file_count"] == 1
        assert result["sample_count"] == 1
        assert result["files"][0]["line"] == "L2"
        exact = ConditionService(connection).filter_files(
            {"load_value": [10.0, 30.0], "speed_ratio": [1.0, 3.0]}
        )
        assert exact["file_count"] == 2
        assert {row["load_value"] for row in exact["files"]} == {10.0, 30.0}
        files.update(record["file_uid"], {"integrity_status": "changed"})
        changed = ConditionService(connection).filter_files(
            integrity_status="changed"
        )
        assert changed["file_count"] == 1
        assert changed["files"][0]["model_name"] == "B"


def test_filename_filter_searches_relative_path_and_keeps_full_counts(database_info):
    service = DatabaseService(database_info.path)
    with service.connect() as connection:
        files = FileRepository(connection)
        samples = SampleRepository(connection)
        first = files.create(
            file_values("line-a/Test_100%.wav", payload="a" * 64)
        )
        second = files.create(
            file_values("line-b/test_100%_second.wav", payload="b" * 64)
        )
        files.create(file_values("line-c/unrelated.wav", payload="c" * 64))
        for index in range(2):
            samples.create(
                {
                    "file_uid": first["file_uid"],
                    "sample_id": f"channel-{index}",
                    "sample_scope": "channel",
                    "display_name": f"Channel {index}",
                    "locator_json": {"channel_index": index},
                }
            )
        samples.create(
            {
                "file_uid": second["file_uid"],
                "sample_id": "main",
                "sample_scope": "whole_file",
                "display_name": "Main",
                "locator_json": {},
            }
        )

        result = ConditionService(connection).filter_files(
            filename="TEST_100%", limit=1, offset=1
        )

        assert result["file_count"] == 2
        assert result["sample_count"] == 3
        assert len(result["files"]) == 1
        assert "test_100%" in result["files"][0]["relative_path"].lower()

        literal_wildcard = ConditionService(connection).filter_files(filename="%")
        assert literal_wildcard["file_count"] == 2


def test_storage_path_guards_and_scan_exclusions(storage_root, storage_roots):
    write_wav(storage_root / "epump2" / "ok.wav")
    write_wav(storage_root / "_ai3" / "databases" / "ignore.wav")
    write_wav(storage_root / "epump2" / "ignore.wav.partial")
    (storage_root / "epump2" / "state.sqlite3-wal").write_bytes(b"sidecar")
    (storage_root / "epump2" / ".ok.wav.ai3-publish.lock").write_bytes(b"lock")
    result = scan_storage(storage_roots=storage_roots)
    assert [entry.relative_path for entry in result.entries] == ["epump2/ok.wav"]
    assert result.excluded_count >= 4
    assert normalize_relative_path("a//b.wav") == "a/b.wav"
    for unsafe in ("/tmp/a.wav", "../a.wav", "a/../b.wav", "C:\\a.wav"):
        with pytest.raises(InvalidRelativePathError):
            normalize_relative_path(unsafe)
