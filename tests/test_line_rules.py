from core.files.sample_discovery.line_rules import channel_mappings_for_path


def test_epump2_channels_come_from_line_rules():
    line, mappings = channel_mappings_for_path(
        "/data/epump2/E-pump_FA6K0265_277797701_06012026_102420.tdms.zst"
    )

    assert line == "epump2"
    assert [(item["sample_id"], item["group_name"], item["channel_name"]) for item in mappings] == [
        ("up", "Vib Up_0", "ACC"),
        ("down", "Vib Down_0", "ACC"),
    ]


def test_etilt1_reference_selects_conditional_channels():
    _, special = channel_mappings_for_path(
        "/data/etilt1/E-Tilt_4031033_20260927_120000_SN001.tdms"
    )
    _, fallback = channel_mappings_for_path(
        "/data/etilt1/E-Tilt_OTHER_20260927_120000_SN001.tdms"
    )

    assert [item["group_name"] for item in special] == ["Measure_9", "Measure_10"]
    assert [item["group_name"] for item in fallback] == ["Measure_1", "Measure_2"]


def test_contents_can_resolve_shared_epump_mapping_without_line_in_path():
    metadata = {
        "groups": [
            {"name": "Vib Up_0", "channels": [{"name": "ACC"}]},
            {"name": "Vib Down_0", "channels": [{"name": "ACC"}]},
        ]
    }

    _, mappings = channel_mappings_for_path("/incoming/pump.tdms", metadata=metadata)

    assert [item["sample_id"] for item in mappings] == ["up", "down"]
