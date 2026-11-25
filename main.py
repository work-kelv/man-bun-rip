"""Man Bun Rip: desktop entry point.

Starts the FastAPI backend on a free localhost port in a background thread,
then opens it inside a native window via pywebview.

    python main.py            # desktop window
    python main.py --server   # backend only (open the printed URL in a browser)
"""
from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn

from backend.paths import APP_NAME
from backend.server import app, set_window


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if not thread.is_alive() or time.time() > deadline:
            raise RuntimeError("The local server failed to start.")
        time.sleep(0.05)
    return server


def main() -> None:
    port = free_port()
    server = start_server(port)
    url = f"http://127.0.0.1:{port}/"

    if "--server" in sys.argv:
        print(f"{APP_NAME} running at {url}  (Ctrl+C to quit)", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        import webview

        window = webview.create_window(APP_NAME, url, width=1100, height=780, min_size=(900, 620),
                                       background_color="#FFFFFF")
        set_window(window)
        webview.start()

    server.should_exit = True


if __name__ == "__main__":
    main()
