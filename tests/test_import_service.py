from __future__ import annotations

from pathlib import Path
import json

import pytest

from core.database import DatabaseService, FileRepository, SampleRepository
from core.database.repositories.files import FileRepository as FileRepositoryClass
import core.database.services.import_service as import_service_module
from tests.helpers import write_tdms, write_wav


def records(database_info):
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        files = FileRepository(connection).list(limit=None)
        sample_repo = SampleRepository(connection)
        return files, {
            row["file_uid"]: sample_repo.list_by_file(row["file_uid"])
            for row in files
        }


def test_external_wav_import_mono_and_multichannel(
    importer, database_info, external_root, storage_root
):
    mono = write_wav(external_root / "mono.wav", channels=1)
    stereo = write_wav(external_root / "stereo.wav", channels=2)
    result = importer.import_paths(
        [mono, stereo],
        target_relative_dir="epump2/20260319",
        conditions={"line": "epump2", "load_value": 12.5, "load_unit": "N"},
    )
    assert result["run"]["success_count"] == 2
    assert result["run"]["status"] == "completed_with_attention"
    assert mono.exists() and stereo.exists()
    files, samples = records(database_info)
    assert {row["relative_path"] for row in files} == {
        "epump2/20260319/mono.wav",
        "epump2/20260319/stereo.wav",
    }
    by_name = {Path(row["relative_path"]).name: row for row in files}
    assert [item["sample_id"] for item in samples[by_name["mono.wav"]["file_uid"]]] == [
        "main"
    ]
    assert samples[by_name["stereo.wav"]["file_uid"]] == []
    assert by_name["mono.wav"]["line"] == "epump2"
    assert by_name["stereo.wav"]["load_value"] == 12.5


def test_import_preview_discovers_samples_without_mutation(
    importer, database_info, external_root, storage_root
):
    source = write_wav(external_root / "preview.wav", channels=1)
    preview = importer.preview_paths(
        [source],
        target_relative_dir="preview",
        conditions={"line": "L-preview"},
    )
    assert preview["mutated"] is False
    assert preview["ready_count"] == 1
    assert preview["items"][0]["samples"][0]["sample_id"] == "main"
    assert not (storage_root / "preview" / "preview.wav").exists()
    assert records(database_info)[0] == []


def test_import_preview_marks_batch_target_collisions_stably(
    importer, database_info, external_root, storage_root
):
    first = write_wav(external_root / "a" / "same.wav", sample_rate=8_000)
    second = write_wav(external_root / "b" / "same.wav", sample_rate=16_000)

    forward = importer.preview_paths(
        [first, second], target_relative_dir="line"
    )
    reverse = importer.preview_paths(
        [second, first], target_relative_dir="line"
    )

    def signature(result):
        return [
            (
                item["source_display_path"],
                item["target_relative_path"],
                item["status"],
                item["payload_sha256"],
                tuple(item["issues"]),
            )
            for item in result["items"]
        ]

    assert signature(forward) == signature(reverse)
    assert forward["ready_count"] == 0
    assert forward["conflict_count"] == 2
    assert {item["target_relative_path"] for item in forward["items"]} == {
        "line/same.wav"
    }
    assert all(
        "multiple sources map to the same target" in item["issues"][-1]
        for item in forward["items"]
    )
    assert not (storage_root / "line" / "same.wav").exists()
    assert records(database_info)[0] == []


@pytest.mark.parametrize("reserved_name", ["line", "extra_fields"])
def test_import_service_rejects_reserved_custom_condition_names(
    importer, reserved_name
):
    with pytest.raises(ValueError, match=f"reserved: {reserved_name}"):
        importer._normalize_conditions(
            {"extra_fields": {reserved_name: "ambiguous"}}
        )


def test_external_tdms_is_compressed_registered_and_source_preserved(
    importer, database_info, external_root, storage_root
):
    source = write_tdms(external_root / "pump.tdms")
    result = importer.import_paths(
        [source],
        target_relative_dir="epump2/day1",
        conditions={"model_name": "M1"},
    )
    assert result["run"]["success_count"] == 1
    assert source.is_file()
    target = storage_root / "epump2" / "day1" / "pump.tdms.zst"
    assert target.is_file()
    files, samples = records(database_info)
    assert files[0]["relative_path"].endswith(".tdms.zst")
    assert not files[0]["relative_path"].lower().endswith(".tdms")
    assert {item["sample_id"] for item in samples[files[0]["file_uid"]]} == {
        "up",
        "down",
    }
    assert files[0]["payload_sha256"] != files[0]["stored_sha256"]


def test_external_move_removes_source_only_after_successful_registration(
    importer, database_info, external_root, storage_root
):
    source = write_wav(external_root / "move-me.wav", channels=1)
    preview = importer.preview_paths(
        [source], target_relative_dir="line", transfer_mode="move"
    )
    assert preview["items"][0]["action"] == "move"
    assert source.is_file()

    result = importer.import_paths(
        [source], target_relative_dir="line", transfer_mode="move"
    )

    assert result["run"]["success_count"] == 1
    assert not source.exists()
    assert (storage_root / "line" / "move-me.wav").is_file()
    assert records(database_info)[0][0]["relative_path"] == "line/move-me.wav"


def test_business_records_follow_the_confirmed_json_contract(
    importer, database_info, external_root
):
    source = write_wav(external_root / "contract.wav", channels=1)
    importer.import_paths(
        [source],
        target_relative_dir="line",
        conditions={
            "line": "epump2",
            "device_id": "daq_01",
            "model_name": "机型A",
            "reference": "reference_A",
            "load_value": 80,
            "load_unit": "%",
            "speed_ratio": 1.0,
            "acquired_at": "2026-09-22T10:30:00+08:00",
        },
    )

    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        file_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(file_records)")
        }
        sample_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sample_records)")
        }
        file_row = connection.execute("SELECT * FROM file_records").fetchone()
        sample_row = connection.execute("SELECT * FROM sample_records").fetchone()

    assert file_columns == {"file_uid", "storage_id", "relative_path", "conditions", "metadata"}
    assert sample_columns == {"file_uid", "sample_id", "sample_scope"}
    assert json.loads(file_row["conditions"])["timestamp"] == "2026-09-22T10:30:00+08:00"
    assert json.loads(file_row["metadata"]) == {}
    sample_scope = json.loads(sample_row["sample_scope"])
    assert sample_scope["start_s"] == 0.0
    assert sample_scope["end_s"] > sample_scope["start_s"]


def test_tdms_changed_after_discovery_is_not_published(
    importer, database_info, external_root, storage_root, monkeypatch
):
    source = write_tdms(external_root / "changes-before-compress.tdms")
    original_compress = import_service_module.compress_tdms

    def replace_then_compress(source_path, target_path, **kwargs):
        write_tdms(Path(source_path), sample_rate=12_345)
        return original_compress(source_path, target_path, **kwargs)

    monkeypatch.setattr(import_service_module, "compress_tdms", replace_then_compress)
    result = importer.import_paths([source], target_relative_dir="line")

    assert result["run"]["failed_count"] == 1
    assert not (storage_root / "line" / "changes-before-compress.tdms.zst").exists()
    assert records(database_info)[0] == []


def test_internal_tdms_deleted_only_after_successful_registration(
    importer, database_info, storage_root
):
    source = write_tdms(storage_root / "epump2" / "inside.tdms")
    result = importer.import_paths([source], conditions={"line": "epump2"})
    assert result["run"]["success_count"] == 1
    assert not source.exists()
    assert source.with_name("inside.tdms.zst").exists()
    files, _ = records(database_info)
    assert files[0]["relative_path"] == "epump2/inside.tdms.zst"


def test_invalid_tdms_leaves_no_file_record_or_formal_target(
    importer, database_info, external_root, storage_root
):
    source = external_root / "broken.tdms"
    source.write_bytes(b"not a tdms")
    result = importer.import_paths([source], target_relative_dir="bad")
    assert result["run"]["failed_count"] == 1
    assert records(database_info)[0] == []
    assert not (storage_root / "bad" / "broken.tdms.zst").exists()
    assert not list(storage_root.rglob("*.partial"))


def test_internal_cleanup_failure_is_recorded_after_successful_commit(
    importer, database_info, storage_root, monkeypatch
):
    source = write_tdms(storage_root / "line" / "cleanup.tdms")
    original_unlink = Path.unlink

    def guarded_unlink(path, *args, **kwargs):
        if (
            path.name == source.name
            and path.parent.name.endswith(".cleanup.partial")
        ):
            raise OSError("injected cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    result = importer.import_paths([source], conditions={"line": "epump2"})
    assert result["run"]["success_count"] == 1
    assert result["run"]["status"] == "completed_with_attention"
    assert result["items"][0]["status"] == "cleanup_pending"
    assert source.exists()
    assert source.with_name("cleanup.tdms.zst").exists()
    assert len(records(database_info)[0]) == 1
    monkeypatch.setattr(Path, "unlink", original_unlink)
    updated = importer.retry_cleanup(result["items"][0]["item_uuid"])
    assert updated["status"] == "registered"
    assert not source.exists()


def test_internal_cleanup_waits_when_target_lock_is_busy(
    importer, database_info, storage_root, monkeypatch
):
    source = write_tdms(storage_root / "line" / "locked-cleanup.tdms")

    def busy_target_lock(_target):
        raise FileExistsError("injected busy target")

    monkeypatch.setattr(
        import_service_module, "acquire_publication_lock", busy_target_lock
    )
    result = importer.import_paths([source], conditions={"line": "epump2"})

    assert result["run"]["success_count"] == 1
    assert result["run"]["status"] == "completed_with_attention"
    assert result["items"][0]["status"] == "cleanup_pending"
    assert source.exists()
    assert source.with_name("locked-cleanup.tdms.zst").exists()
    assert len(records(database_info)[0]) == 1


def test_internal_cleanup_refuses_a_replaced_source(
    importer, database_info, storage_root, monkeypatch
):
    source = write_tdms(storage_root / "line" / "replaced-before-cleanup.tdms")
    original_cleanup = importer._remove_verified_internal_tdms

    def replace_then_cleanup(connection, *, source, prepared, file_uid):
        write_tdms(source, sample_rate=12_345)
        return original_cleanup(
            connection,
            source=source,
            prepared=prepared,
            file_uid=file_uid,
        )

    monkeypatch.setattr(importer, "_remove_verified_internal_tdms", replace_then_cleanup)
    result = importer.import_paths([source], conditions={"line": "epump2"})

    assert result["run"]["success_count"] == 1
    assert result["run"]["status"] == "completed_with_attention"
    assert result["items"][0]["status"] == "cleanup_pending"
    assert source.exists()
    assert source.with_name("replaced-before-cleanup.tdms.zst").exists()


def test_internal_cleanup_does_not_delete_a_new_source_created_after_quarantine(
    importer, database_info, storage_root, monkeypatch
):
    source = write_tdms(storage_root / "line" / "arrives-during-cleanup.tdms")
    original_publish = import_service_module.publish_noreplace
    publish_calls = []
    recreated = []

    def publish_then_recreate(partial, target):
        publish_calls.append((str(partial), str(target)))
        result = original_publish(partial, target)
        if not recreated:
            write_tdms(source, sample_rate=12_345)
            recreated.append(source.exists())
        return result

    monkeypatch.setattr(
        import_service_module, "publish_noreplace", publish_then_recreate
    )
    result = importer.import_paths([source], conditions={"line": "epump2"})

    assert result["run"]["success_count"] == 1
    assert recreated, result["items"][0]
    assert source.exists(), (publish_calls, recreated, result)
    assert source.with_name("arrives-during-cleanup.tdms.zst").exists()


def test_published_file_is_recoverable_after_database_failure(
    importer, database_info, external_root, storage_root, monkeypatch
):
    source = write_wav(external_root / "recover.wav")
    original_create = FileRepositoryClass.create

    def fail_create(self, values):
        raise RuntimeError("injected database failure")

    monkeypatch.setattr(FileRepositoryClass, "create", fail_create)
    first = importer.import_paths([source], target_relative_dir="recovery")
    assert first["run"]["failed_count"] == 1
    assert first["items"][0]["status"] == "published_pending_registration"
    target = storage_root / "recovery" / "recover.wav"
    assert target.exists()
    monkeypatch.setattr(FileRepositoryClass, "create", original_create)
    second = importer.import_paths([source], target_relative_dir="recovery")
    assert second["run"]["success_count"] == 1
    assert len(records(database_info)[0]) == 1


def test_same_name_different_content_is_conflict_without_overwrite(
    importer, database_info, external_root, storage_root
):
    first_source = write_wav(external_root / "same.wav", sample_rate=8_000)
    first = importer.import_paths([first_source], target_relative_dir="line")
    assert first["run"]["success_count"] == 1
    target = storage_root / "line" / "same.wav"
    original = target.read_bytes()
    write_wav(first_source, sample_rate=16_000)
    second = importer.import_paths([first_source], target_relative_dir="line")
    assert second["run"]["conflict_count"] == 1
    assert target.read_bytes() == original
    assert len(records(database_info)[0]) == 1


def test_missing_registered_target_conflict_does_not_publish_new_content(
    importer, database_info, external_root, storage_root
):
    source = write_wav(external_root / "orphan.wav", sample_rate=8_000)
    first = importer.import_paths([source], target_relative_dir="line")
    assert first["run"]["success_count"] == 1
    target = storage_root / "line" / "orphan.wav"
    target.unlink()
    write_wav(source, sample_rate=16_000)

    second = importer.import_paths([source], target_relative_dir="line")

    assert second["run"]["conflict_count"] == 1
    assert not target.exists()
    assert len(records(database_info)[0]) == 1
