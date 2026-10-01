from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from core.database import DatabaseService, FileRepository, ImportService
from web.api import main
from web.api.runtime import RuntimeState
from web.api.models import (
    ConditionUpdateRequest,
    Conditions,
    DatabaseCreateRequest,
    DatabaseFolderSelectRequest,
    DatabaseLineCreateRequest,
    DatabaseOpenRequest,
    DatabaseUpdateRequest,
    ImportRequest,
    LabelEventCreateRequest,
    LabelSessionCreateRequest,
    ReconcileApplyRequest,
    StorageFolderSelectRequest,
    TdmsLabelResolveRequest,
)
from tests.helpers import write_wav


def test_event_and_whole_labels_keep_distinct_scope_kinds(tmp_path):
    source = write_wav(tmp_path / "two-modes.wav")
    session_path = main.create_labeling_session(LabelSessionCreateRequest(paths=[str(source)], output_directory=str(tmp_path), source="operator"))["session_path"]
    common = dict(
        path=str(source), line="epump2", source_sample_id="audio", source="operator", session_path=session_path,
        result_key="ok", reason_key="clean_normal",
    )
    whole = main.create_label_event(LabelEventCreateRequest(
        **common, sample_id="audio_whole", scope_kind="whole",
        sample_scope={"start_s": 0.0, "end_s": 0.05},
    ))
    event = main.create_label_event(LabelEventCreateRequest(
        **common, sample_id="audio_001", scope_kind="event",
        sample_scope={"start_s": 0.01, "end_s": 0.02},
    ))
    repeated = main.create_label_event(LabelEventCreateRequest(
        **common, sample_id="audio_001", scope_kind="event",
        sample_scope={"start_s": 0.01, "end_s": 0.02},
    ))
    assert whole["scope_kind"] == "whole"
    assert event["scope_kind"] == "event"
    assert repeated["sample_id"] == event["sample_id"]
    assert repeated["event_uuid"] != event["event_uuid"]
    saved = main._read_standalone_labels(Path(session_path))["files"][0]
    assert {item["sample_id"]: item["label_events"][-1]["scope_kind"] for item in saved["samples"]} == {
        "audio_whole": "whole", "audio_001": "event",
    }
    assert len(next(item for item in saved["samples"] if item["sample_id"] == "audio_001")["label_events"]) == 2
    with pytest.raises(HTTPException) as error:
        main.create_label_event(LabelEventCreateRequest(
            **common, sample_id="audio_whole", scope_kind="whole",
            sample_scope={"start_s": 0.01, "end_s": 0.02},
        ))
    assert error.value.status_code == 422


def test_whole_wav_label_covers_full_signal(tmp_path):
    source = tmp_path / "trimmed.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes((b"\x00\x10" * 8_000) * 4)
    common = dict(
        path=str(source), line="epump2", source_sample_id="audio", sample_id="audio_whole",
        scope_kind="whole", source="operator", result_key="ok", reason_key="clean_normal",
    )
    with pytest.raises(HTTPException) as missing_session:
        main.create_label_event(LabelEventCreateRequest(**common, sample_scope={"start_s": 0.0, "end_s": 4.0}))
    assert missing_session.value.status_code == 422
    common["session_path"] = main.create_labeling_session(LabelSessionCreateRequest(paths=[str(source)], output_directory=str(tmp_path), source="operator"))["session_path"]
    saved = main.create_label_event(LabelEventCreateRequest(
        **common, sample_scope={"start_s": 0.0, "end_s": 4.0},
    ))
    assert saved["sample_scope"] == {"start_s": 0.0, "end_s": 4.0}
    with pytest.raises(HTTPException) as error:
        main.create_label_event(LabelEventCreateRequest(
            **common, sample_scope={"start_s": 0.5, "end_s": 3.5},
        ))
    assert error.value.status_code == 422


def test_single_label_json_picker_detects_taxonomy_or_history(tmp_path, monkeypatch):
    taxonomy = tmp_path / "label.json"
    taxonomy.write_text(json.dumps({
        "results": [{"result_key": "ok", "result_id": 0, "result_name": "正常"}],
        "reasons": [],
    }), encoding="utf-8")
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"files": []}), encoding="utf-8")
    for path, kind in ((taxonomy, "taxonomy"), (history, "history")):
        monkeypatch.setattr(main, "_choose_files_macos", lambda _title, selected=path: [selected])
        result = main.select_labeling_json()
        assert result["path"] == str(path)
        assert result["kind"] == kind


def test_event_audio_is_cropped_and_repeatable(tmp_path):
    source = write_wav(tmp_path / "event.wav")
    sample = main._label_file_samples(source)[0]
    raw = main._read_label_signal(source, sample)
    cut = main.cut_and_normalize_rms(raw, sample["sampling_rate_hz"])
    start = cut.start_s + 0.01
    end = cut.start_s + 0.03

    for _ in range(2):
        response = main.labeling_audio(str(source), sample["sample_id"], start, end)
        assert float(response.headers["X-Audio-Start-S"]) == pytest.approx(start)
        assert float(response.headers["X-Audio-End-S"]) == pytest.approx(end)
        with wave.open(io.BytesIO(response.body)) as audio:
            assert audio.getframerate() == 8_000
            assert audio.getnframes() == 160

    with pytest.raises(HTTPException) as error:
        main.labeling_audio(str(source), sample["sample_id"], end, start)
    assert error.value.status_code == 422


def test_tdms_audio_trims_then_normalizes_without_peak_rescaling(tmp_path, monkeypatch):
    source = tmp_path / "sample.tdms"
    source.touch()
    raw = np.concatenate([np.full(50, 0.8), np.full(100, 0.02), np.full(50, 0.8)]).astype(np.float32)
    monkeypatch.setattr(main, "_label_file_samples", lambda *_args: [{"sample_id": "up", "sampling_rate_hz": 100}])
    monkeypatch.setattr(main, "_read_label_signal", lambda *_args: raw)

    response = main.labeling_audio(str(source), "up")
    assert float(response.headers["X-Audio-Start-S"]) == 0.5
    assert float(response.headers["X-Audio-End-S"]) == 1.5
    with wave.open(io.BytesIO(response.body)) as audio:
        values = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2") / 32767
    assert len(values) == 100
    assert np.sqrt(np.mean(values ** 2)) == pytest.approx(0.3, abs=1 / 32767)


def test_labeling_session_keeps_existing_standalone_annotations(tmp_path):
    source = write_wav(tmp_path / "existing.wav")
    sidecar = main._standalone_label_path(source)
    main._write_standalone_labels(
        sidecar,
        {
            "file_uid": "existing-file",
            "storage_id": "standalone",
            "relative_path": source.name,
            "prototype": False,
            "conditions": {},
            "metadata": {},
            "samples": [
                {
                    "sample_id": "audio_001",
                    "sample_scope": {"start_s": 0.005, "end_s": 0.02},
                    "label_events": [{"event_uuid": "existing-event", "source": "operator"}],
                }
            ],
        },
    )

    created = main.create_labeling_session(
        LabelSessionCreateRequest(
            paths=[str(source)],
            output_directory=str(tmp_path),
            source="operator",
        )
    )
    session = main._read_standalone_labels(Path(created["session_path"]))

    assert re.fullmatch(r"label_\d{8}_\d{6}(?:_\d+)?\.json", Path(created["session_path"]).name)
    assert len(session["files"]) == 1
    assert session["files"][0]["file_uid"] == "existing-file"
    assert session["files"][0]["relative_path"] == source.name
    assert session["files"][0]["samples"][0]["sample_id"] == "audio_001"
    assert session["files"][0]["samples"][0]["label_events"][0]["event_uuid"] == "existing-event"

    resolved = main.resolve_labeling_file(
        TdmsLabelResolveRequest(path=str(source), session_path=created["session_path"])
    )
    assert resolved["annotations"][0]["sample_id"] == "audio_001"
    assert resolved["samples"][0]["label_events"][0]["event_uuid"] == "existing-event"


def test_selected_history_is_copied_to_timestamped_label_without_overwriting_it(tmp_path):
    source = write_wav(tmp_path / "sample.wav")
    history = tmp_path / "old_labels.json"
    history.write_text(json.dumps({"files": [{"relative_path": source.name, "samples": []}]}), encoding="utf-8")
    request = LabelSessionCreateRequest(paths=[str(source)], output_directory=str(tmp_path), history_path=str(history), source="operator")
    created = main.create_labeling_session(request)
    output = Path(created["session_path"])
    assert re.fullmatch(r"label_\d{8}_\d{6}(?:_\d+)?\.json", output.name)
    assert json.loads(history.read_text(encoding="utf-8"))["files"][0]["relative_path"] == source.name
    assert main._read_standalone_labels(output)["files"][0]["relative_path"] == source.name
    assert main.create_labeling_session(LabelSessionCreateRequest(paths=[str(source)], output_directory=str(tmp_path), history_path=str(output), source="operator"))["session_path"] == str(output)


def test_last_selected_timestamped_label_is_reopened(tmp_path):
    source = write_wav(tmp_path / "sample.wav")
    output = tmp_path / "label_20261001_103000.json"
    remembered = tmp_path / "remembered.json"
    output.write_text(json.dumps({"files": [{"relative_path": source.name, "samples": [{"sample_id": "audio_001", "label_events": [{"event_uuid": "current"}]}]}]}), encoding="utf-8")
    remembered.write_text(json.dumps({"files": [{"relative_path": source.name, "samples": [{"sample_id": "audio_001", "label_events": [{"event_uuid": "old"}]}]}]}), encoding="utf-8")

    created = main.create_labeling_session(LabelSessionCreateRequest(paths=[str(source)], output_directory=str(tmp_path), history_path=str(output), source="operator"))

    assert created["session_path"] == str(output)
    assert main._read_standalone_labels(output)["files"][0]["samples"][0]["label_events"][0]["event_uuid"] == "current"


def test_existing_label_time_is_loaded_before_first_save(tmp_path):
    source = write_wav(tmp_path / "sample.wav")
    output = tmp_path / "label_time.json"
    main._write_standalone_labels(output, {"files": [{"relative_path": source.name, "samples": [{"sample_id": "audio_001", "label_events": [{"event_uuid": "previous"}]}]}]})
    resolved = main.resolve_labeling_file(TdmsLabelResolveRequest(path=str(source), session_path=str(output)))
    assert resolved["annotations"][0]["label_events"][0]["event_uuid"] == "previous"


def list_files(
    *, database_uid=None, line=None, filename=None, load_value=None, speed_ratio=None
):
    return main.list_files(
        line=line or [],
        device_id=[],
        model_name=[],
        reference=[],
        load_value=load_value or [],
        load_unit=[],
        speed_ratio=speed_ratio or [],
        acquired_at=[],
        filename=filename,
        load_min=None,
        load_max=None,
        speed_min=None,
        speed_max=None,
        availability_status=None,
        integrity_status=None,
        limit=500,
        offset=0,
        database_uid=database_uid,
    )


def test_api_requires_matching_preview_before_import(
    database_info, storage_root, external_root, monkeypatch
):
    main.state.register_database(
        database_info.database_uid, database_info.database_name, database_info.path
    )
    main.state.add_database_line(database_info.database_uid, "L-api")
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    monkeypatch.setattr(main.state, "previews", {})
    source = write_wav(external_root / "api.wav")
    request = ImportRequest(
        source_paths=[str(source)],
        target_relative_dir="L-api",
        source_file_type="wav",
        conditions=Conditions(line="L-api"),
    )
    with pytest.raises(HTTPException) as missing_preview:
        main.import_files(request)
    assert missing_preview.value.status_code == 422

    preview = main.import_preview(request)
    preview_file = preview["result"]["items"][0]
    assert set(preview_file) == {
        "file_uid", "storage_id", "relative_path", "conditions", "metadata", "samples"
    }
    assert preview_file["conditions"]["timestamp"] is None
    assert set(preview_file["samples"][0]) == {
        "sample_id", "sample_scope", "label_events"
    }
    assert not (storage_root / "L-api" / "api.wav").exists()
    stale_confirmation = ImportRequest(
        **request.model_dump(exclude={"preview_id"}),
        preview_id=preview["preview_id"],
    )
    write_wav(source, sample_rate=16_000)
    with pytest.raises(HTTPException) as stale_preview:
        main.import_files(stale_confirmation)
    assert stale_preview.value.status_code == 422
    assert not (storage_root / "L-api" / "api.wav").exists()

    preview = main.import_preview(request)
    confirmed = ImportRequest(
        **request.model_dump(exclude={"preview_id"}),
        preview_id=preview["preview_id"],
    )
    result = main.import_files(confirmed)
    assert result["result"]["success_count"] == 1
    assert result["result"]["files"][0]["file_uid"] == preview["result"]["items"][0]["file_uid"]
    assert (storage_root / "L-api" / "api.wav").exists()


def test_create_database_line_updates_catalog_and_folder(database_info, storage_root):
    main.state.register_database(
        database_info.database_uid, database_info.database_name, database_info.path
    )

    result = main.create_database_line(
        database_info.database_uid, DatabaseLineCreateRequest(name="epump4")
    )

    assert result == {"line": "epump4", "lines": ["epump4"]}
    assert (storage_root / "epump4").is_dir()
    saved = main.state.list_registered_databases()
    assert saved[0]["lines"] == ["epump4"]


def test_switching_database_invalidates_runtime_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("AI3_STORAGE_ROOT", str(tmp_path))
    runtime = RuntimeState()
    first = tmp_path / "first.sqlite3"
    second = tmp_path / "second.sqlite3"
    runtime.set_database(first)
    assert runtime.storage_root() == tmp_path.resolve()
    runtime.put_preview({"preview_id": "preview-1"})

    runtime.set_database(second)

    assert runtime.storage_root() is None
    with pytest.raises(KeyError):
        runtime.get_preview("preview-1")


def test_unavailable_default_storage_starts_without_selected_root(tmp_path, monkeypatch):
    config = tmp_path / "storage.json"
    config.write_text(
        '{"wuxi_raw":"/Volumes/ai3-test-volume-that-is-not-mounted/data"}',
        encoding="utf-8",
    )
    monkeypatch.delenv("AI3_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("AI3_STORAGE_CONFIG", str(config))

    runtime = RuntimeState()

    assert runtime.storage_root() is None
    assert runtime.storage_roots == {}


def test_switching_storage_root_keeps_database_and_invalidates_previews(tmp_path, monkeypatch):
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    first_root.mkdir()
    second_root.mkdir()
    monkeypatch.setenv("AI3_STORAGE_ROOT", str(first_root))
    runtime = RuntimeState()
    runtime.set_database(first_root / "_ai3" / "databases" / "first.sqlite3")
    runtime.put_preview({"preview_id": "preview-before-root-switch"})

    runtime.set_storage_root(second_root)

    assert runtime.storage_root() == second_root.resolve()
    assert runtime.current_database == (
        first_root / "_ai3" / "databases" / "first.sqlite3"
    ).resolve()
    with pytest.raises(KeyError):
        runtime.get_preview("preview-before-root-switch")


def test_pending_database_home_record_survives_runtime_restart(tmp_path, monkeypatch):
    state_path = tmp_path / "catalog-state.json"
    monkeypatch.setenv("AI3_CATALOG_STATE", str(state_path))
    runtime = RuntimeState()
    runtime.create_pending_database("pending-uid", "pending-name")

    restarted = RuntimeState()

    assert restarted.list_pending_databases() == [
        {
            "database_uid": "pending-uid",
            "database_name": "pending-name",
            "default_storage_id": "wuxi_raw",
            "schema_version": None,
            "path": None,
            "sqlite_path": None,
            "database_root": None,
            "pending_path": True,
            "active": False,
        }
    ]


def test_select_storage_folder_migrates_empty_self_contained_database(
    tmp_path, monkeypatch
):
    catalog_root = tmp_path / "catalog"
    storage_root = catalog_root / "chosen"
    destination = tmp_path / "moved-database"
    storage_root.mkdir(parents=True)
    destination.mkdir()
    database = DatabaseService.create_database(
        database_name="chosen", storage_root=storage_root
    )
    # External FAT/exFAT volumes may contain an AppleDouble sidecar next to
    # the real SQLite file.  It must not be counted as a second database.
    database.path.with_name(f"._{database.path.name}").write_bytes(b"sidecar")
    monkeypatch.setattr(main.state, "database_roots", [catalog_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "current_database", database.path)
    monkeypatch.setattr(
        main.state, "previews", {"stale": {"preview_id": "stale"}}
    )

    response = main.select_storage_folder(
        StorageFolderSelectRequest(path=str(destination))
    )

    expected_database = destination / "databases" / "chosen.sqlite3"
    assert response["root_path"] == str(destination.resolve())
    assert response["database_root"] == str(destination.resolve())
    assert response["sqlite_path"] == str(expected_database.resolve())
    assert response["database"]["path"] == response["sqlite_path"]
    assert response["selected"] is True
    assert response["mounted"] is True
    assert response["cancelled"] is False
    assert "databases" not in response
    assert main.state.current_database == expected_database.resolve()
    assert main.state.storage_root() == destination.resolve()
    assert main.state.previews == {}
    assert expected_database.is_file()
    assert not storage_root.exists()
    assert Path(response["previous_root_backup"]).is_dir()


def test_select_storage_folder_rejects_nonempty_database_without_moving_it(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "source-database"
    destination = tmp_path / "destination"
    external = tmp_path / "external"
    source_root.mkdir()
    destination.mkdir()
    external.mkdir()
    database = DatabaseService.create_database(
        database_name="has-files", storage_root=source_root
    )
    ImportService(
        database.path,
        storage_roots={"wuxi_raw": source_root},
        profiles_dir=main.state.profiles_dir,
    ).import_paths([write_wav(external / "recorded.wav")])
    monkeypatch.setattr(main.state, "database_roots", [source_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": source_root})
    monkeypatch.setattr(main.state, "current_database", database.path)
    monkeypatch.setattr(
        main.state, "previews", {"keep": {"preview_id": "keep"}}
    )

    with pytest.raises(HTTPException) as error:
        main.select_storage_folder(
            StorageFolderSelectRequest(path=str(destination))
        )

    assert error.value.status_code == 409
    assert "已有文件记录" in error.value.detail
    assert database.path.is_file()
    assert list(destination.iterdir()) == []
    assert main.state.current_database == database.path
    assert "keep" in main.state.previews


def test_select_storage_folder_never_overwrites_destination(tmp_path, monkeypatch):
    source_root = tmp_path / "source-database"
    destination = tmp_path / "destination"
    source_root.mkdir()
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("do not overwrite", encoding="utf-8")
    database = DatabaseService.create_database(
        database_name="empty", storage_root=source_root
    )
    monkeypatch.setattr(main.state, "database_roots", [source_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": source_root})
    monkeypatch.setattr(main.state, "current_database", database.path)

    with pytest.raises(HTTPException) as error:
        main.select_storage_folder(
            StorageFolderSelectRequest(path=str(destination))
        )

    assert error.value.status_code == 409
    assert marker.read_text(encoding="utf-8") == "do not overwrite"
    assert database.path.is_file()


def test_import_source_picker_cancellation_is_a_normal_response(
    database_info, monkeypatch
):
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    monkeypatch.setattr(main, "_choose_files_macos", lambda prompt: None)
    monkeypatch.setattr(main, "_choose_folder_macos", lambda prompt: None)

    assert main.select_import_sources(mode="files") == {
        "mode": "files",
        "paths": [],
        "cancelled": True,
    }
    assert main.select_import_sources(mode="folder") == {
        "mode": "folder",
        "paths": [],
        "cancelled": True,
    }


def test_reconcile_preview_api_exposes_folder_scope(
    importer, database_info, storage_root, external_root, monkeypatch
):
    importer.import_paths(
        [write_wav(external_root / "scoped.wav")], target_relative_dir="line"
    )
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "previews", {})

    preview = main.reconcile_preview(scope="folder", relative_path="line")

    assert preview["scope"] == "folder"
    assert preview["scope_relative_path"] == "line"
    assert preview["preview_id"] in main.state.previews


def test_file_path_options_use_active_records_without_scanning_storage(
    importer, database_info, storage_root, external_root, monkeypatch
):
    imported_paths: list[str] = []
    for index, (name, target_dir) in enumerate(
        (
            ("pump_A.wav", "Area/A"),
            ("pump_b.wav", "Area/B"),
            ("pump_C.wav", "Area/C"),
            ("other.wav", "Other/Deep"),
        )
    ):
        imported = importer.import_paths(
            [write_wav(external_root / name, sample_rate=8_000 + index * 1_000)],
            target_relative_dir=target_dir,
        )
        imported_paths.append(str(imported["files"][0]["relative_path"]))

    # Registered paths remain selectable even if the physical file is absent,
    # while an unregistered physical-only file must not appear.
    (storage_root / imported_paths[0]).unlink()
    write_wav(storage_root / "Physical" / "unregistered.wav")

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("path options must not call scan_storage")

    monkeypatch.setattr(main, "scan_storage", unexpected_scan)

    monkeypatch.setattr(main.state, "database_roots", [storage_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "current_database", database_info.path)

    result = main.file_path_options(
        database_uid=database_info.database_uid,
        file_query="PUMP_",
        file_limit=2,
    )

    assert result == {
        "database_uid": database_info.database_uid,
        "files": [
            "Area/A/pump_A.wav",
            "Area/B/pump_b.wav",
        ],
        "folders": [
            "Area",
            "Area/A",
            "Area/B",
            "Area/C",
            "Other",
            "Other/Deep",
        ],
        "matched_file_count": 3,
        "files_truncated": True,
        "physical_scan_available": False,
    }


def test_file_path_options_query_is_literal_and_default_limit_is_not_truncated(
    importer, database_info, storage_root, external_root, monkeypatch
):
    imported = importer.import_paths(
        [write_wav(external_root / "run_100%.wav")], target_relative_dir="line"
    )
    recorded_path = str(imported["files"][0]["relative_path"])
    monkeypatch.setattr(main.state, "current_database", database_info.path)

    assert main.file_path_options(file_query="100%") == {
        "database_uid": database_info.database_uid,
        "files": [recorded_path],
        "folders": ["line"],
        "matched_file_count": 1,
        "files_truncated": False,
        "physical_scan_available": False,
    }


def test_file_path_options_reject_invalid_direct_file_limit(
    database_info, monkeypatch
):
    monkeypatch.setattr(main.state, "current_database", database_info.path)

    with pytest.raises(HTTPException) as too_small:
        main.file_path_options(file_limit=0)
    assert too_small.value.status_code == 422

    with pytest.raises(HTTPException) as too_large:
        main.file_path_options(file_limit=201)
    assert too_large.value.status_code == 422


def test_storage_and_database_lists_are_friendly_without_selection(monkeypatch):
    monkeypatch.setattr(main.state, "storage_roots", {})
    monkeypatch.setattr(main.state, "current_database", None)

    storage = main.storage_status()
    databases = main.list_databases()

    assert storage["root_path"] is None
    assert storage["selected"] is False
    assert "尚未配置" in storage["message"]
    assert isinstance(databases["databases"], list)


def test_name_only_create_waits_for_folder_then_uses_direct_databases_directory(
    tmp_path, monkeypatch
):
    catalog_root = tmp_path / "app-databases"
    destination = tmp_path / "chosen-root"
    destination.mkdir()
    marker = destination / "raw-file.keep"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(main.state, "database_roots", [catalog_root])
    monkeypatch.setattr(main.state, "catalog_state_path", tmp_path / "catalog-state.json")
    monkeypatch.setattr(main.state, "hidden_database_paths", set())
    monkeypatch.setattr(main.state, "pending_databases", {})
    monkeypatch.setattr(main.state, "current_pending_database_uid", None)
    monkeypatch.setattr(main.state, "storage_roots", {})
    monkeypatch.setattr(main.state, "current_database", None)
    monkeypatch.setattr(main.state, "previews", {})

    created = main.create_database(DatabaseCreateRequest(name="local-only"))

    assert created["pending_path"] is True
    assert created["path"] is None
    assert not catalog_root.exists()
    assert main.list_databases()["databases"][0]["database_uid"] == created["database_uid"]

    selected = main.select_storage_folder(
        StorageFolderSelectRequest(path=str(destination))
    )

    expected = destination / "databases" / "local-only.sqlite3"
    assert selected["sqlite_path"] == str(expected.resolve())
    assert expected.is_file()
    assert not (destination / "_ai3").exists()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert main.state.current_database == expected.resolve()
    assert main.state.storage_root() == destination.resolve()

    removed = main.remove_database_record(created["database_uid"])
    assert removed["sqlite_deleted"] is False
    assert removed["raw_files_deleted"] is False
    assert expected.is_file()
    assert marker.is_file()
    assert created["database_uid"] not in {
        item["database_uid"] for item in main.list_databases()["databases"]
    }

    reopened = main.open_database(DatabaseOpenRequest(path=str(expected)))
    assert reopened["database_uid"] == created["database_uid"]
    assert created["database_uid"] in {
        item["database_uid"] for item in main.list_databases()["databases"]
    }


def test_database_refresh_reads_catalog_only_and_checks_saved_path(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    existing = DatabaseService.create_database(database_name="existing", storage_root=root)
    missing_path = root / "databases" / "missing.sqlite3"
    monkeypatch.setattr(main.state, "registered_databases", {})
    main.state.register_database(existing.database_uid, existing.database_name, existing.path)
    main.state.register_database("missing-uid", "missing", missing_path)

    monkeypatch.setattr(
        main,
        "_managed_database_roots",
        lambda: (_ for _ in ()).throw(AssertionError("refresh must not scan folders")),
    )

    records = {item["database_uid"]: item for item in main.list_databases()["databases"]}
    assert records[existing.database_uid]["path_exists"] is True
    assert records[existing.database_uid]["error"] is None
    assert records["missing-uid"]["path_exists"] is False
    assert "不存在" in records["missing-uid"]["error"]


def test_delete_record_only_updates_catalog_when_storage_is_missing(tmp_path, monkeypatch):
    missing_path = tmp_path / "offline" / "databases" / "missing.sqlite3"
    monkeypatch.setattr(main.state, "registered_databases", {})
    main.state.register_database("offline-uid", "offline", missing_path)
    monkeypatch.setattr(
        main,
        "_managed_database_roots",
        lambda: (_ for _ in ()).throw(AssertionError("delete must not scan storage")),
    )

    removed = main.remove_database_record("offline-uid")

    assert removed == {
        "database_uid": "offline-uid",
        "removed": True,
        "path": str(missing_path.resolve(strict=False)),
        "sqlite_deleted": False,
        "raw_files_deleted": False,
    }
    assert main.list_databases()["databases"] == []


def test_pending_database_accepts_selecting_the_databases_directory(
    tmp_path, monkeypatch
):
    catalog_root = tmp_path / "app-databases"
    destination = tmp_path / "chosen-root"
    databases_directory = destination / "databases"
    databases_directory.mkdir(parents=True)
    monkeypatch.setattr(main.state, "database_roots", [catalog_root])
    monkeypatch.setattr(main.state, "catalog_state_path", tmp_path / "catalog-state.json")
    monkeypatch.setattr(main.state, "hidden_database_paths", set())
    monkeypatch.setattr(main.state, "pending_databases", {})
    monkeypatch.setattr(main.state, "current_pending_database_uid", None)
    monkeypatch.setattr(main.state, "storage_roots", {})
    monkeypatch.setattr(main.state, "current_database", None)
    monkeypatch.setattr(main.state, "previews", {})

    main.create_database(DatabaseCreateRequest(name="inside-databases"))
    selected = main.select_storage_folder(
        StorageFolderSelectRequest(path=str(databases_directory))
    )

    expected = databases_directory / "inside-databases.sqlite3"
    assert selected["database_root"] == str(destination.resolve())
    assert selected["sqlite_path"] == str(expected.resolve())
    assert expected.is_file()
    assert not (databases_directory / "databases").exists()


def test_database_folder_selection_and_open_bind_database_own_root(
    tmp_path, monkeypatch
):
    app_root = tmp_path / "app-root"
    external_catalog = tmp_path / "external-catalog"
    data_root = tmp_path / "raw-data"
    app_root.mkdir()
    external_catalog.mkdir()
    data_root.mkdir()
    active = DatabaseService.create_database(database_name="active", storage_root=app_root)
    discovered = DatabaseService.create_database(
        database_name="discovered", storage_root=external_catalog
    )
    monkeypatch.setattr(main.state, "database_roots", [app_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": data_root})
    monkeypatch.setattr(main.state, "current_database", active.path)
    monkeypatch.setattr(main.state, "previews", {})

    response = main.select_database_folder(
        DatabaseFolderSelectRequest(
            path=str(external_catalog / "databases")
        )
    )

    assert response["catalog_root"] == str(external_catalog.resolve())
    assert [item["database_uid"] for item in response["databases"]] == [
        discovered.database_uid
    ]
    assert main.state.storage_root() == data_root.resolve()
    assert main.state.current_database == active.path

    opened = main.open_database(DatabaseOpenRequest(path=str(discovered.path)))
    assert opened["database_uid"] == discovered.database_uid
    assert opened["database_root"] == str(external_catalog.resolve())
    assert opened["sqlite_path"] == str(discovered.path)
    assert main.state.storage_root() == external_catalog.resolve()
    assert main.summary(discovered.database_uid)["database_open"] is True


def test_selecting_data_storage_requires_open_database(tmp_path, monkeypatch):
    data_root = tmp_path / "raw-data"
    data_root.mkdir()
    monkeypatch.setattr(main.state, "storage_roots", {})
    monkeypatch.setattr(main.state, "current_database", None)

    with pytest.raises(HTTPException) as error:
        main.select_storage_folder(StorageFolderSelectRequest(path=str(data_root)))

    assert error.value.status_code == 409
    assert "先新建或打开数据库" in error.value.detail


def test_database_uid_keeps_data_panel_queries_scoped(
    database_info, storage_root, storage_roots, external_root, monkeypatch
):
    second = DatabaseService.create_database(
        database_name="second", storage_root=storage_root
    )
    source = write_wav(external_root / "second.wav")
    imported = ImportService(
        second.path,
        storage_roots=storage_roots,
        profiles_dir=main.state.profiles_dir,
    ).import_paths(
        [source],
        target_relative_dir="second",
        conditions={"line": "L2", "load_value": 25.0, "speed_ratio": 2.5},
    )
    file_uid = imported["files"][0]["file_uid"]

    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "database_roots", [storage_root])
    monkeypatch.setattr(main.state, "current_database", database_info.path)

    assert main.summary()["file_count"] == 0
    assert main.summary(second.database_uid)["file_count"] == 1

    scoped_files = list_files(database_uid=second.database_uid)
    assert [row["file_uid"] for row in scoped_files["files"]] == [file_uid]
    assert list_files(
        database_uid=second.database_uid, filename="SECOND.WA"
    )["file_count"] == 1
    assert list_files(
        database_uid=second.database_uid, filename="%"
    )["file_count"] == 0
    assert list_files(
        database_uid=second.database_uid, load_value=[25.0], speed_ratio=[2.5]
    )["file_count"] == 1
    assert list_files(
        database_uid=second.database_uid, load_value=[20.0, 30.0]
    )["file_count"] == 0

    changed = main.update_conditions(
        ConditionUpdateRequest(
            file_uids=[file_uid], conditions=Conditions(line="L2-edited")
        ),
        database_uid=second.database_uid,
    )
    assert changed == {"updated_count": 1}
    assert list_files()["files"] == []
    assert list_files(
        database_uid=second.database_uid, line=["L2-edited"]
    )["file_count"] == 1


def test_condition_options_are_scoped_sorted_typed_and_empty_safe(
    database_info, importer, storage_root, storage_roots, external_root, monkeypatch
):
    first_rows = (
        (
            "later.wav",
            {
                "line": "Line-Z",
                "device_id": "Collect-2",
                "reference": "Project-2",
                "model_name": "Model-2",
                "load_value": 20,
                "speed_ratio": 2,
                "acquired_at": "2026-09-23T11:00:00+08:00",
            },
        ),
        (
            "earlier.wav",
            {
                "line": "Line-A",
                "device_id": "Collect-1",
                "reference": "Project-1",
                "model_name": "Model-1",
                "load_value": 10.5,
                "speed_ratio": 1.25,
                "acquired_at": "2026-09-23T10:00:00+08:00",
            },
        ),
    )
    for name, conditions in first_rows:
        importer.import_paths(
            [write_wav(external_root / name)],
            target_relative_dir="first",
            conditions=conditions,
        )

    # Whitespace-only text and inactive records must not leak into choices.
    blank = importer.import_paths(
        [write_wav(external_root / "blank.wav")],
        target_relative_dir="first",
    )["files"][0]
    inactive = importer.import_paths(
        [write_wav(external_root / "inactive.wav")],
        target_relative_dir="first",
        conditions={"line": "Inactive-Line", "load_value": 999},
    )["files"][0]
    with DatabaseService(database_info.path).connect() as connection:
        repository = FileRepository(connection)
        repository.update(blank["file_uid"], {"line": "   "})
        repository.update(inactive["file_uid"], {"record_status": "superseded"})

    second = DatabaseService.create_database(
        database_name="condition-options-second", storage_root=storage_root
    )
    ImportService(
        second.path,
        storage_roots=storage_roots,
        profiles_dir=main.state.profiles_dir,
    ).import_paths(
        [write_wav(external_root / "foreign.wav")],
        target_relative_dir="second",
        conditions={"line": "Foreign-Line", "load_value": 30},
    )
    empty = DatabaseService.create_database(
        database_name="condition-options-empty", storage_root=storage_root
    )

    monkeypatch.setattr(main.state, "database_roots", [storage_root])
    monkeypatch.setattr(main.state, "current_database", database_info.path)

    result = main.file_condition_options(database_uid=database_info.database_uid)

    assert result == {
        "database_uid": database_info.database_uid,
        "options": {
            "line": ["Line-A", "Line-Z"],
            "device_id": ["Collect-1", "Collect-2"],
            "reference": ["Project-1", "Project-2"],
            "model_name": ["Model-1", "Model-2"],
            "load_value": [10.5, 20.0],
            "load_unit": [],
            "speed_ratio": [1.25, 2.0],
            "acquired_at": [
                "2026-09-23T10:00:00+08:00",
                "2026-09-23T11:00:00+08:00",
            ],
        },
    }
    assert main.file_condition_options(database_uid=second.database_uid)["options"] == {
        "line": ["Foreign-Line"],
        "device_id": [],
        "reference": [],
        "model_name": [],
        "load_value": [30.0],
        "load_unit": [],
        "speed_ratio": [],
        "acquired_at": [],
    }
    assert main.file_condition_options(database_uid=empty.database_uid) == {
        "database_uid": empty.database_uid,
        "options": {
            "line": [],
            "device_id": [],
            "reference": [],
            "model_name": [],
            "load_value": [],
            "load_unit": [],
            "speed_ratio": [],
            "acquired_at": [],
        },
    }


def test_database_uid_keeps_path_options_reconcile_preview_and_apply_scoped(
    tmp_path, external_root, monkeypatch
):
    catalog_root = tmp_path / "catalog"
    first_root = catalog_root / "first"
    second_root = catalog_root / "second"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    first = DatabaseService.create_database(
        database_name="first", storage_root=first_root
    )
    second = DatabaseService.create_database(
        database_name="second", storage_root=second_root
    )

    first_batch = ImportService(
        first.path,
        storage_roots={"wuxi_raw": first_root},
        profiles_dir=main.state.profiles_dir,
    ).import_paths(
        [write_wav(external_root / "first.wav")], target_relative_dir="first-line"
    )
    second_batch = ImportService(
        second.path,
        storage_roots={"wuxi_raw": second_root},
        profiles_dir=main.state.profiles_dir,
    ).import_paths(
        [write_wav(external_root / "second.wav")], target_relative_dir="second-line"
    )
    first_file = first_batch["files"][0]
    second_file = second_batch["files"][0]
    second_relative_path = str(second_file["relative_path"])
    (second_root / second_relative_path).unlink()

    monkeypatch.setattr(main.state, "database_roots", [catalog_root])
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": first_root})
    monkeypatch.setattr(main.state, "current_database", first.path)
    monkeypatch.setattr(main.state, "previews", {})

    options = main.file_path_options(database_uid=second.database_uid)
    assert options["files"] == [second_relative_path]
    assert str(first_file["relative_path"]) not in options["files"]

    preview = main.reconcile_preview(
        scope="file",
        relative_path=second_relative_path,
        database_uid=second.database_uid,
    )
    assert preview["database_path"] == str(second.path)
    assert preview["storage_root"] == str(second_root.resolve())
    assert [item["file_uid"] for item in preview["items"]] == [
        second_file["file_uid"]
    ]
    assert preview["items"][0]["category"] == "missing"

    action = {
        "item_id": preview["items"][0]["item_id"],
        "action": "mark_missing",
    }
    with pytest.raises(HTTPException) as wrong_database:
        main.reconcile_apply(
            ReconcileApplyRequest(
                preview_id=preview["preview_id"],
                database_uid=first.database_uid,
                actions=[action],
            )
        )
    assert "preview belongs to another database" in wrong_database.value.detail

    applied = main.reconcile_apply(
        ReconcileApplyRequest(
            preview_id=preview["preview_id"],
            database_uid=second.database_uid,
            actions=[action],
        )
    )
    assert applied["applied_count"] == 1

    with DatabaseService(first.path).connect(readonly=True) as connection:
        first_record = FileRepository(connection).get(str(first_file["file_uid"]))
    with DatabaseService(second.path).connect(readonly=True) as connection:
        second_record = FileRepository(connection).get(str(second_file["file_uid"]))
    assert first_record is not None
    assert first_record["availability_status"] == "present"
    assert second_record is not None
    assert second_record["availability_status"] == "missing"


def test_database_name_update_preserves_sqlite_path(
    database_info, storage_root, monkeypatch
):
    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "database_roots", [storage_root])
    monkeypatch.setattr(main.state, "current_database", database_info.path)
    original_path = database_info.path

    response = main.update_database(
        database_info.database_uid,
        DatabaseUpdateRequest(name="renamed display name"),
    )

    assert response["database_name"] == "renamed display name"
    assert Path(response["path"]) == original_path
    assert original_path.is_file()
    reopened = DatabaseService.open_database(
        original_path,
        storage_root=storage_root,
        expected_storage_id="wuxi_raw",
    )
    assert reopened.database_name == "renamed display name"


def test_database_uid_resolution_rejects_outside_symlink_and_wrong_storage(
    tmp_path, storage_root, monkeypatch
):
    outside_root = tmp_path / "outside-storage"
    outside_root.mkdir()
    outside = DatabaseService.create_database(
        database_name="outside", storage_root=outside_root
    )
    wrong_storage = DatabaseService.create_database(
        database_name="wrong-storage",
        storage_root=storage_root,
        storage_id="another_storage",
    )
    (storage_root / "databases" / "outside-link.sqlite3").symlink_to(
        outside.path
    )

    monkeypatch.setattr(main.state, "storage_roots", {"wuxi_raw": storage_root})
    monkeypatch.setattr(main.state, "database_roots", [storage_root])
    monkeypatch.setattr(main.state, "current_database", None)

    with pytest.raises(HTTPException) as outside_error:
        main.summary(outside.database_uid)
    assert outside_error.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_storage_error:
        main.summary(wrong_storage.database_uid)
    assert wrong_storage_error.value.status_code == 404
