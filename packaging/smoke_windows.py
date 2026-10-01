"""Exercise the packaged Windows WAV reader and Mel calculation."""

import json
import math
from pathlib import Path
import struct
import tempfile
from urllib.parse import urlencode
from urllib.request import urlopen
import wave


path = Path(tempfile.gettempdir()) / "ai3-packaging-smoke-rfw.wav"
try:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(4)
        output.setsampwidth(2)
        output.setframerate(25_600)
        values = [value for index in range(25_600) for value in (int(1000 * math.sin(index / 23)), 0, 0, 0)]
        output.writeframes(struct.pack(f"<{len(values)}h", *values))
    query = urlencode({"path": str(path), "sample_id": "channel_1", "wav_profile": "motor"})
    with urlopen(f"http://127.0.0.1:18030/api/labeling/waveform?{query}", timeout=120) as response:
        payload = json.load(response)
    assert len(payload["values"]) == 25_600
    assert len(payload["mel_db"]) == 39
finally:
    path.unlink(missing_ok=True)
