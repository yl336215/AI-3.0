from pathlib import Path

from web.api.waveform_cache import WaveformDiskCache


def test_waveform_cache_keeps_recent_items_and_invalidates_changed_source(tmp_path: Path):
    cache = WaveformDiskCache(tmp_path / "cache", max_items=2)
    sources = [tmp_path / f"item-{index}.wav" for index in range(3)]
    for source in sources:
        source.write_bytes(b"audio")
    variant = {"sample_id": "channel_1", "filter": False}

    cache.put(sources[0], variant, {"values": [1]})
    cache.put(sources[1], variant, {"values": [2]})
    assert cache.get(sources[0], variant) == {"values": [1]}
    cache.put(sources[2], variant, {"values": [3]})
    assert cache.get(sources[0], variant) == {"values": [1]}
    assert cache.get(sources[1], variant) is None
    assert cache.get(sources[2], variant) == {"values": [3]}

    sources[0].write_bytes(b"changed audio")
    assert cache.get(sources[0], variant) is None
