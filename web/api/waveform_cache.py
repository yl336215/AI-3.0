"""Bounded on-disk cache for annotation waveforms.

The browser keeps only the current item's plots and samples. Cached responses
live here so revisiting an item does not require recomputing its spectrograms.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any


class WaveformDiskCache:
    def __init__(self, root: Path, max_items: int = 20):
        self.root = root
        self.max_items = max_items
        self._lock = RLock()

    def _paths(self, source: Path, variant: dict[str, Any]) -> tuple[Path, Path]:
        stat = source.stat()
        item_id = hashlib.sha256(str(source).encode()).hexdigest()
        identity = {"version": 1, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, **variant}
        variant_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        directory = self.root / item_id
        return directory, directory / f"{variant_id}.json.gz"

    def get(self, source: Path, variant: dict[str, Any]) -> dict[str, Any] | None:
        try:
            directory, path = self._paths(source, variant)
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            os.utime(directory, None)
            return payload
        except (OSError, ValueError, EOFError, TypeError):
            return None

    def put(self, source: Path, variant: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            directory, path = self._paths(source, variant)
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as raw:
                temporary = Path(raw.name)
            try:
                with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as stream:
                    json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            with self._lock:
                os.utime(directory, None)
                items = sorted((item for item in self.root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime)
                for stale in items[: max(0, len(items) - self.max_items)]:
                    for entry in stale.iterdir():
                        entry.unlink(missing_ok=True)
                    stale.rmdir()
        except OSError:
            # The cache is optional; a read-only or full temp volume must not
            # interrupt annotation.
            return


waveform_cache = WaveformDiskCache(Path(tempfile.gettempdir()) / "ai3-waveform-cache")
