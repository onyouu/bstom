"""Find an available local port; never open another app's localhost page."""
import socket
import webbrowser
from threading import Thread

import uvicorn


def choose_port():
    for port in range(8878, 8900):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No available port between 8878 and 8899")


def open_when_ready(port):
    import time
    import urllib.request
    import json
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1) as response:
                if json.load(response).get("name") == "Bstom":
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return
        except Exception:
            time.sleep(0.2)


if __name__ == "__main__":
    selected = choose_port()
    print(f"Bstom: http://127.0.0.1:{selected}")
    Thread(target=open_when_ready, args=(selected,), daemon=True).start()
    uvicorn.run("bstom.app:app", host="127.0.0.1", port=selected)
