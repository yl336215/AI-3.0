from __future__ import annotations

from pathlib import Path

import pytest
import zstandard as zstd

import core.database.services.reconcile_service as reconcile_module
from core.database import (
    DatabaseService,
    FileRepository,
    ReconcileConflict,
    ReconcileService,
    SampleRepository,
)
from core.files import StorageRoots, StorageUnavailableError
from core.files.readers.tdms import iter_decompressed_chunks
from tests.helpers import write_tdms, write_wav


def reconciler(database_info, storage_roots) -> ReconcileService:
    return ReconcileService(
        database_info.path,
        storage_roots=storage_roots,
        profiles_dir=Path(__file__).resolve().parents[1] / "config" / "sample_profiles",
    )


def active_record(database_info):
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        return FileRepository(connection).list(limit=None)[0]


def test_move_keeps_file_uid_and_conditions_then_missing_keeps_sample(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "move.wav")
    importer.import_paths(
        [source], target_relative_dir="old", conditions={"line": "L1", "reference": "R1"}
    )
    before = active_record(database_info)
    old_path = storage_root / before["relative_path"]
    new_path = storage_root / "new" / "moved.wav"
    new_path.parent.mkdir()
    old_path.rename(new_path)

    service = reconciler(database_info, storage_roots)
    preview = service.preview()
    move = next(item for item in preview["items"] if item["category"] == "move")
    service.apply(
        preview,
        [{"item_id": move["item_id"], "action": "update_path"}],
    )
    after = active_record(database_info)
    assert after["file_uid"] == before["file_uid"]
    assert after["relative_path"] == "new/moved.wav"
    assert after["line"] == "L1" and after["reference"] == "R1"

    payload = new_path.read_bytes()
    new_path.unlink()
    preview = service.preview()
    missing = next(item for item in preview["items"] if item["category"] == "missing")
    service.apply(
        preview,
        [{"item_id": missing["item_id"], "action": "mark_missing"}],
    )
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        record = FileRepository(connection).get(before["file_uid"])
        samples = SampleRepository(connection).list_by_file(before["file_uid"])
    assert record["availability_status"] == "missing"
    assert len(samples) == 1
    assert samples[0]["availability_status"] == "missing"

    new_path.write_bytes(payload)
    preview = service.preview()
    restored = next(item for item in preview["items"] if item["category"] == "restored")
    service.apply(
        preview,
        [{"item_id": restored["item_id"], "action": "mark_present"}],
    )
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        record = FileRepository(connection).get(before["file_uid"])
        samples = SampleRepository(connection).list_by_file(before["file_uid"])
    assert record["availability_status"] == "present"
    assert samples[0]["availability_status"] == "present"


def test_content_replacement_creates_new_file_uid_and_supersedes_old(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "replace.wav", sample_rate=8_000)
    importer.import_paths([source], target_relative_dir="line", conditions={"line": "old"})
    old = active_record(database_info)
    stored = storage_root / old["relative_path"]
    write_wav(stored, sample_rate=16_000)

    service = reconciler(database_info, storage_roots)
    preview = service.preview()
    replacement = next(
        item for item in preview["items"] if item["category"] == "content_replaced"
    )
    result = service.apply(
        preview,
        [{"item_id": replacement["item_id"], "action": "supersede_and_register"}],
        replacement_conditions={"line": "new"},
    )
    assert result["results"][0]["new_file_uid"] != old["file_uid"]
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        all_rows = FileRepository(connection).list(record_status=None, limit=None)
    by_uid = {row["file_uid"]: row for row in all_rows}
    assert by_uid[old["file_uid"]]["record_status"] == "superseded"
    active = next(row for row in all_rows if row["record_status"] == "active")
    assert active["line"] == "new"
    assert active["relative_path"] == old["relative_path"]


def test_ambiguous_move_can_be_resolved_by_explicit_candidate(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "ambiguous.wav")
    importer.import_paths([source], target_relative_dir="old", conditions={"line": "L1"})
    old = active_record(database_info)
    old_path = storage_root / old["relative_path"]
    payload = old_path.read_bytes()
    old_path.unlink()
    candidate_a = storage_root / "new" / "a.wav"
    candidate_b = storage_root / "new" / "b.wav"
    candidate_a.parent.mkdir()
    candidate_a.write_bytes(payload)
    candidate_b.write_bytes(payload)

    service = reconciler(database_info, storage_roots)
    preview = service.preview()
    ambiguous = next(item for item in preview["items"] if item["category"] == "ambiguous")
    assert set(ambiguous["candidates"]) == {"new/a.wav", "new/b.wav"}
    service.apply(
        preview,
        [
            {
                "item_id": ambiguous["item_id"],
                "action": "update_path",
                "target_relative_path": "new/b.wav",
            }
        ],
    )
    selected = active_record(database_info)
    assert selected["file_uid"] == old["file_uid"]
    assert selected["relative_path"] == "new/b.wav"
    assert selected["line"] == "L1"


def test_scoped_preview_uses_segment_boundaries_and_ignores_outside_move_candidate(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_wav(external_root / "scoped.wav")
    importer.import_paths([source], target_relative_dir="line")
    record = active_record(database_info)
    stored = storage_root / record["relative_path"]
    payload = stored.read_bytes()
    stored.unlink()
    outside_candidate = storage_root / "line-other" / "same.wav"
    outside_candidate.parent.mkdir()
    outside_candidate.write_bytes(payload)

    service = reconciler(database_info, storage_roots)
    preview = service.preview(scope="folder", relative_path="line")

    assert preview["scope"] == "folder"
    assert preview["scope_relative_path"] == "line"
    assert preview["counts"] == {"missing": 1}
    assert preview["items"][0]["old_relative_path"] == "line/scoped.wav"
    assert all(
        "line-other" not in str(item.get("new_relative_path") or "")
        for item in preview["items"]
    )


def test_folder_scope_only_scans_and_fingerprints_the_selected_tree(
    importer,
    database_info,
    storage_roots,
    external_root,
    storage_root,
    monkeypatch,
):
    importer.import_paths(
        [write_wav(external_root / "inside.wav")], target_relative_dir="line"
    )
    importer.import_paths(
        [write_wav(external_root / "outside.wav")],
        target_relative_dir="line-other",
    )

    original_scan = reconcile_module.scan_storage
    scan_scopes: list[str] = []

    def tracked_scan(*args, **kwargs):
        scan_scopes.append(str(kwargs.get("relative_dir", "")))
        return original_scan(*args, **kwargs)

    original_fingerprint = reconcile_module.fingerprint_file
    fingerprinted: list[Path] = []

    def tracked_fingerprint(path, *, kind):
        fingerprinted.append(Path(path))
        return original_fingerprint(path, kind=kind)

    monkeypatch.setattr(reconcile_module, "scan_storage", tracked_scan)
    monkeypatch.setattr(reconcile_module, "fingerprint_file", tracked_fingerprint)

    preview = reconciler(database_info, storage_roots).preview(
        scope="folder", relative_path="line"
    )

    assert scan_scopes == ["line"]
    assert fingerprinted
    assert all(
        path.relative_to(storage_root).parts[0] == "line" for path in fingerprinted
    )
    assert preview["counts"] == {"unchanged": 1}


def test_file_scope_checks_only_the_exact_path_without_walking_siblings(
    importer,
    database_info,
    storage_roots,
    external_root,
    storage_root,
    monkeypatch,
):
    importer.import_paths(
        [write_wav(external_root / "selected.wav")], target_relative_dir="line"
    )
    importer.import_paths(
        [write_wav(external_root / "sibling.wav")], target_relative_dir="line"
    )

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("file scope must not call scan_storage")

    original_fingerprint = reconcile_module.fingerprint_file
    fingerprinted: list[Path] = []

    def tracked_fingerprint(path, *, kind):
        fingerprinted.append(Path(path))
        return original_fingerprint(path, kind=kind)

    monkeypatch.setattr(reconcile_module, "scan_storage", unexpected_scan)
    monkeypatch.setattr(reconcile_module, "fingerprint_file", tracked_fingerprint)

    preview = reconciler(database_info, storage_roots).preview(
        scope="file", relative_path="line/selected.wav"
    )

    assert [path.relative_to(storage_root).as_posix() for path in fingerprinted] == [
        "line/selected.wav"
    ]
    assert preview["counts"] == {"unchanged": 1}
    assert preview["items"][0]["old_relative_path"] == "line/selected.wav"


def test_file_scope_uses_strict_single_path_semantics_for_a_move(
    importer, database_info, storage_roots, external_root, storage_root
):
    importer.import_paths(
        [write_wav(external_root / "old.wav")], target_relative_dir="line"
    )
    old_path = storage_root / "line" / "old.wav"
    new_path = storage_root / "line" / "new.wav"
    old_path.rename(new_path)

    preview = reconciler(database_info, storage_roots).preview(
        scope="file", relative_path="line/old.wav"
    )

    assert preview["counts"] == {"missing": 1}
    assert preview["items"][0]["old_relative_path"] == "line/old.wav"
    assert all(item["category"] != "move" for item in preview["items"])


def test_folder_scope_still_detects_a_move_within_the_selected_tree(
    importer, database_info, storage_roots, external_root, storage_root
):
    importer.import_paths(
        [write_wav(external_root / "old.wav")], target_relative_dir="line/old"
    )
    old_path = storage_root / "line" / "old" / "old.wav"
    new_path = storage_root / "line" / "new" / "moved.wav"
    new_path.parent.mkdir()
    old_path.rename(new_path)

    preview = reconciler(database_info, storage_roots).preview(
        scope="folder", relative_path="line"
    )

    assert preview["counts"] == {"move": 1}
    assert preview["items"][0]["old_relative_path"] == "line/old/old.wav"
    assert preview["items"][0]["new_relative_path"] == "line/new/moved.wav"


def test_file_scope_keeps_new_file_and_bare_tdms_actions(
    database_info, storage_roots, storage_root
):
    write_wav(storage_root / "new.wav")
    write_tdms(storage_root / "raw.tdms")
    service = reconciler(database_info, storage_roots)

    wav_preview = service.preview(scope="file", relative_path="new.wav")
    tdms_preview = service.preview(scope="file", relative_path="raw.tdms")

    assert wav_preview["counts"] == {"pending_register": 1}
    assert wav_preview["items"][0]["new_relative_path"] == "new.wav"
    assert tdms_preview["counts"] == {"pending_compress": 1}
    assert tdms_preview["items"][0]["old_relative_path"] == "raw.tdms"


@pytest.mark.parametrize(
    "scope,relative_path",
    [
        ("folder", "../line"),
        ("folder", "/absolute/line"),
        ("file", "line/../../escape.wav"),
        ("file", ""),
    ],
)
def test_scoped_preview_rejects_unsafe_or_empty_relative_path(
    database_info, storage_roots, scope, relative_path
):
    service = reconciler(database_info, storage_roots)
    with pytest.raises(ValueError):
        service.preview(scope=scope, relative_path=relative_path)


def test_recompression_keeps_file_uid_samples_and_conditions(
    importer, database_info, storage_roots, external_root, storage_root
):
    source = write_tdms(external_root / "recompressed.tdms")
    importer.import_paths(
        [source], target_relative_dir="line", conditions={"model_name": "M1"}
    )
    before = active_record(database_info)
    target = storage_root / before["relative_path"]
    logical_payload = b"".join(iter_decompressed_chunks(target))
    target.write_bytes(zstd.ZstdCompressor(level=1, write_checksum=False).compress(logical_payload))

    service = reconciler(database_info, storage_roots)
    preview = service.preview()
    item = next(row for row in preview["items"] if row["category"] == "recompressed")
    service.apply(
        preview,
        [{"item_id": item["item_id"], "action": "accept_recompression"}],
    )
    after = active_record(database_info)
    assert after["file_uid"] == before["file_uid"]
    assert after["model_name"] == "M1"
    assert after["payload_sha256"] == before["payload_sha256"]
    assert after["stored_sha256"] != before["stored_sha256"]
    with DatabaseService(database_info.path).connect(readonly=True) as connection:
        samples = SampleRepository(connection).list_by_file(after["file_uid"])
    assert {sample["sample_id"] for sample in samples} == {"up", "down"}


def test_unavailable_storage_does_not_mark_everything_missing(
    tmp_path, storage_roots
):
    root = storage_roots.root("wuxi_raw")
    info = DatabaseService.create_database(
        database_name="outside",
        storage_root=root,
    )
    unavailable_roots = StorageRoots({"wuxi_raw": tmp_path / "unmounted"})
    service = ReconcileService(info.path, storage_roots=unavailable_roots)
    with pytest.raises(StorageUnavailableError):
        service.preview()


def test_apply_rejects_a_database_record_changed_after_preview(
    importer, database_info, storage_roots, external_root
):
    source = write_wav(external_root / "stale.wav")
    importer.import_paths([source], target_relative_dir="line")
    record = active_record(database_info)
    with DatabaseService(database_info.path).connect() as connection:
        FileRepository(connection).update(
            record["file_uid"], {"availability_status": "missing"}
        )

    service = reconciler(database_info, storage_roots)
    preview = service.preview()
    restored = next(item for item in preview["items"] if item["category"] == "restored")
    with DatabaseService(database_info.path).connect() as connection:
        FileRepository(connection).update(
            record["file_uid"], {"relative_path": "moved/stale.wav"}
        )

    with pytest.raises(ReconcileConflict):
        service.apply(
            preview,
            [{"item_id": restored["item_id"], "action": "mark_present"}],
        )
