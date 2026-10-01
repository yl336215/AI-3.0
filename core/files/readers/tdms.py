"""Standalone TDMS and TDMS.ZST readers for AI-3.0."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

import zstandard as zstd
from nptdms import TdmsFile
import numpy as np


COPY_CHUNK_SIZE = 4 * 1024 * 1024
SPOOL_MEMORY_LIMIT = 64 * 1024 * 1024


def is_compressed_tdms(path: str | os.PathLike[str]) -> bool:
    return Path(path).name.lower().endswith(".tdms.zst")


def iter_decompressed_chunks(
    path: str | os.PathLike[str], *, chunk_size: int = COPY_CHUNK_SIZE
) -> Iterator[bytes]:
    """Yield the complete decompressed Zstandard payload."""

    source = Path(path)
    with source.open("rb") as compressed_stream:
        with zstd.ZstdDecompressor().stream_reader(compressed_stream) as reader:
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                yield chunk


@contextmanager
def materialize_tdms(
    path: str | os.PathLike[str],
    *,
    compressed: bool | None = None,
    spool_memory_limit: int = SPOOL_MEMORY_LIMIT,
    temp_dir: str | os.PathLike[str] | None = None,
) -> Iterator[Path | BinaryIO]:
    """Yield a seekable TDMS input, decompressing Zstandard when necessary."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    use_compression = is_compressed_tdms(source) if compressed is None else bool(compressed)
    if not use_compression:
        yield source
        return

    spill_directory = Path(temp_dir).expanduser() if temp_dir is not None else source.parent
    with tempfile.SpooledTemporaryFile(
        max_size=int(spool_memory_limit),
        mode="w+b",
        dir=spill_directory,
        prefix=f".{source.name}.",
        suffix=".partial",
    ) as spool:
        with source.open("rb") as compressed_stream:
            with zstd.ZstdDecompressor().stream_reader(compressed_stream) as reader:
                shutil.copyfileobj(reader, spool, length=COPY_CHUNK_SIZE)
        spool.flush()
        spool.seek(0)
        yield spool


@contextmanager
def open_tdms(
    path: str | os.PathLike[str],
    *,
    compressed: bool | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> Iterator[TdmsFile]:
    """Open a TDMS source without importing any AI-2.0 module."""

    with materialize_tdms(path, compressed=compressed, temp_dir=temp_dir) as seekable:
        with TdmsFile.open(seekable) as tdms:
            yield tdms


def tdms_metadata(
    path: str | os.PathLike[str],
    *,
    compressed: bool | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    groups: list[dict[str, object]] = []
    with open_tdms(path, compressed=compressed, temp_dir=temp_dir) as tdms:
        for group in tdms.groups():
            channels: list[dict[str, object]] = []
            for channel in group.channels():
                length = int(len(channel))
                sampling_rate_hz, duration_s = channel_timing(channel)
                channels.append(
                    {
                        "name": channel.name,
                        "length": length,
                        "sampling_rate_hz": sampling_rate_hz,
                        "duration_s": duration_s,
                    }
                )
            groups.append({"name": group.name, "channels": channels})
    return {"groups": groups}


def channel_timing(channel) -> tuple[float | None, float | None]:
    """Return sample rate and duration from a TDMS channel's waveform increment."""
    try:
        increment = float(channel.properties.get("wf_increment"))
        if increment > 0:
            return 1.0 / increment, len(channel) * increment
    except (TypeError, ValueError):
        pass
    return None, None


def read_tdms_channel(path, group_name: str, channel_name: str) -> tuple[float | None, np.ndarray]:
    """Read an entire TDMS channel without applying labeling-specific cropping."""
    with open_tdms(path) as tdms:
        channel = tdms[group_name][channel_name]
        rate, _ = channel_timing(channel)
        return rate, np.asarray(channel[:], dtype=np.float32)
