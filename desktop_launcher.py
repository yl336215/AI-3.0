"""Start the standalone Windows annotation app and open its local UI."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def _port() -> int:
    configured = os.environ.get("AI3_PORT", "").strip()
    if configured:
        return int(configured)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _open_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)


def main() -> None:
    multiprocessing.freeze_support()
    user_root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AI-3.0"
    for name, folder in (("NUMBA_CACHE_DIR", "numba-cache"), ("MPLCONFIGDIR", "matplotlib-cache")):
        path = user_root / folder
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(name, str(path))
    from web.api.main import app
    import uvicorn

    port = _port()
    url = f"http://127.0.0.1:{port}/"
    if os.environ.get("AI3_NO_BROWSER") != "1":
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    print(f"AI-3.0 音频标注：{url}\n关闭此窗口即可退出。", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, loop="asyncio", http="h11", ws="none", access_log=False)


if __name__ == "__main__":
    main()
