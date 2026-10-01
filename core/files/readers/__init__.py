"""Supported raw-file readers."""

from pathlib import Path

from .tdms import channel_timing, iter_decompressed_chunks, materialize_tdms, open_tdms, read_tdms_channel, tdms_metadata
from .wav import describe_wav, read_wav_channel, read_wav_metadata


def read_signal(path, locator):
    """Return (sample rate, complete float32 signal) for TDMS or WAV.

    WAV type scaling is performed by the WAV reader; TDMS values are
    returned in their stored units. ``locator`` comes from sample discovery.
    """
    if Path(path).name.lower().endswith(".wav"):
        return read_wav_channel(path, int(locator["channel_index"]), locator.get("profile"))
    return read_tdms_channel(path, str(locator["group_name"]), str(locator["channel_name"]))

__all__ = [
    "iter_decompressed_chunks",
    "materialize_tdms",
    "open_tdms",
    "channel_timing",
    "read_tdms_channel",
    "read_signal",
    "describe_wav",
    "read_wav_channel",
    "read_wav_metadata",
    "tdms_metadata",
]
