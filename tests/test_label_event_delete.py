"""Deleting one annotation must preserve other versions of the same sample."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests.helpers import write_wav
from web.api.main import create_label_event, create_labeling_session, delete_label_event
from web.api.models import LabelEventCreateRequest, LabelEventDeleteRequest, LabelSessionCreateRequest


def test_delete_only_selected_event_uuid(tmp_path):
    source = write_wav(tmp_path / "sample.wav")
    session = create_labeling_session(LabelSessionCreateRequest(
        paths=[str(source)], output_directory=str(tmp_path), source="operator",
    ))["session_path"]
    label = dict(path=str(source), line="", source_sample_id="audio", sample_id="audio_001",
                 sample_scope={"start_s": 0.01, "end_s": 0.02}, session_path=session,
                 source="operator", result_key="ok", reason_key="clean_normal")
    first = create_label_event(LabelEventCreateRequest(**label))
    second = create_label_event(LabelEventCreateRequest(**label))

    target = dict(path=str(source), session_path=session, sample_id="audio_001")
    result = delete_label_event(LabelEventDeleteRequest(**target, event_uuid=first["event_uuid"]))
    assert result["remaining_events"] == 1
    saved = json.loads(Path(session).read_text(encoding="utf-8"))
    assert [item["event_uuid"] for item in saved["files"][0]["samples"][0]["label_events"]] == [second["event_uuid"]]

    with pytest.raises(HTTPException) as error:
        delete_label_event(LabelEventDeleteRequest(**target, event_uuid=first["event_uuid"]))
    assert error.value.status_code == 404

    delete_label_event(LabelEventDeleteRequest(**target, event_uuid=second["event_uuid"]))
    saved = json.loads(Path(session).read_text(encoding="utf-8"))
    assert saved["files"][0]["samples"] == []
