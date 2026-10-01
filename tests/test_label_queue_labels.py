"""The queue filter reads saved event summaries without loading audio."""

import json

from web.api.main import labeling_queue_labels


def test_queue_labels_keeps_result_and_reason_in_the_same_event(tmp_path):
    source = tmp_path / "example.wav"
    source.write_bytes(b"audio is not opened by this endpoint")
    label_path = tmp_path / "label_time.json"
    label_path.write_text(json.dumps({"files": [{
        "relative_path": source.name,
        "samples": [{"sample_id": "channel_1", "label_events": [
            {"result_key": "ok", "result_name": "正常", "reason_key": None, "reason_name": None},
            {"result_key": "nok", "result_name": "异常", "reason_key": "noise", "reason_name": "异音"},
        ]}],
    }]}), encoding="utf-8")

    payload = labeling_queue_labels(str(label_path))
    assert payload["files"] == [{"path": str(source), "events": [
        {"result_key": "ok", "result_name": "正常", "reason_key": None, "reason_name": None},
        {"result_key": "nok", "result_name": "异常", "reason_key": "noise", "reason_name": "异音"},
    ]}]
