"""Starting annotation recovers from a missing remembered JSON."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from web.api.main import create_labeling_session
from web.api.models import LabelSessionCreateRequest


def test_missing_remembered_label_json_is_created(tmp_path):
    source = tmp_path / "example.wav"
    source.write_bytes(b"placeholder")
    request = LabelSessionCreateRequest(
        paths=[str(source)], output_directory=str(tmp_path),
        history_path=str(tmp_path / "missing.json"), source="expert",
    )

    first = create_labeling_session(request)
    output = Path(first["session_path"])
    request.history_path = str(output)
    assert first["session_path"] == str(output)
    assert json.loads(output.read_text(encoding="utf-8")) == {"files": []}

    output.write_text('{"files": [{"relative_path": "example.wav", "samples": []}]}\n')
    create_labeling_session(request)
    assert len(json.loads(output.read_text(encoding="utf-8"))["files"]) == 1

    output.write_text('{"results": []}\n')
    with pytest.raises(HTTPException) as error:
        create_labeling_session(request)
    assert error.value.status_code == 422
