"""One-command local start; no crawl, inference or upload."""

import argparse
import socket
import time
import webbrowser
from threading import Thread

import uvicorn

from .app import create_app
from .prepare import ROOT


def open_when_ready(port):
    """Open only after the local server answers, including on a cold file-backed start."""
    import http.client

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            conn.request("GET", "/")
            if conn.getresponse().status == 200:
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        time.sleep(0.25)
    print(
        "Browserstart wartet auf einen erreichbaren lokalen Server; Startprotokoll prüfen.",
        flush=True,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", default=str(ROOT / "generated/gold-studio"))
    p.add_argument("--study", help="Frozen prospective candidate directory")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--simulation", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    try:
        connection = socket.create_connection(("127.0.0.1", a.port), timeout=0.25)
    except OSError:
        pass
    else:
        connection.close()
        raise SystemExit(
            f"Port {a.port} ist bereits belegt. "
            "Bestehendes Studio zuerst beenden oder --port wählen."
        )
    app = create_app(a.workspace, a.simulation, a.study)
    print(f"Zugangscodes nur lokal: {a.workspace}/Zugangscodes.txt", flush=True)
    print(f"Browser: http://127.0.0.1:{a.port}", flush=True)
    if not a.no_browser:
        Thread(target=open_when_ready, args=[a.port], daemon=True).start()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=a.port,
        log_level="warning",
        loop="asyncio",
        http="h11",
        ws="none",
    )


if __name__ == "__main__":
    main()
