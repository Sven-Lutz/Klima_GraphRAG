"""Private loopback research application; model inference requires explicit configuration."""

import argparse
import errno
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--source-presentation", choices=["archived", "linked_originals"], default="archived"
    )
    parser.add_argument("--research-dir", type=Path, default=Path("generated/local-research"))
    parser.add_argument("--technical-validation", action="store_true")
    parser.add_argument(
        "--generation-config", type=Path, help="Explicit private loopback model configuration"
    )
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.generation_config and args.source_presentation != "archived" and not args.stop:
        parser.error("Model generation requires the private archived local composition")
    pidfile = args.research_dir / "preview-process.json"
    if args.stop:
        if not pidfile.exists():
            print("Kein registrierter lokaler Research-Dienst.")
            return
        record = json.loads(pidfile.read_text())
        result = subprocess.run(
            ["ps", "-p", str(record["pid"]), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print("Registrierter Dienst ist bereits beendet; keine andere PID angefasst.")
            return
        if "-m crawler.web.preview" not in result.stdout or str(args.manifest) not in result.stdout:
            raise SystemExit(
                "PID gehört nicht zum erwarteten lokalen Research-Dienst; nicht beendet."
            )
        os.kill(record["pid"], signal.SIGTERM)
        for _ in range(50):
            state = subprocess.run(
                ["ps", "-p", str(record["pid"]), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            )
            if state.returncode != 0 or state.stdout.strip().startswith("Z"):
                if pidfile.exists() and json.loads(pidfile.read_text()).get("pid") == record["pid"]:
                    pidfile.unlink()
                print("Lokaler Research-Dienst beendet; Reviewdaten bleiben gespeichert.")
                return
            time.sleep(0.1)
        raise SystemExit(
            "Dienst beendet noch laufende Arbeit; Status prüfen, nicht blind erneut starten."
        )
    if shutil.disk_usage(args.manifest.parent).free < 15 * 1024**3:
        raise SystemExit("Systemreserve unterschritten. Preview nicht gestartet.")
    args.research_dir.mkdir(parents=True, exist_ok=True)
    lock = (args.research_dir / "preview.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(
            "Der lokale Research-Dienst läuft bereits. Keine zweite Instanz gestartet."
        ) from None
    with socket.socket() as probe:
        # Match uvicorn's reuse policy: completed local HTTP connections may
        # remain in TIME_WAIT after a clean stop and must not prevent restart.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise SystemExit(
                    "Port belegt. Bestehenden Dienst prüfen; keine zweite Instanz starten."
                ) from None
            raise SystemExit(
                f"Lokaler Socketstart nicht erlaubt oder nicht verfügbar (errno={exc.errno}). "
                "Ausführungsrechte und Sandbox prüfen; kein Dienst gestartet."
            ) from None
    import uvicorn

    from crawler.web.app import create_app
    from crawler.web.bavaria import create_bavaria_service

    if args.source_presentation == "archived":
        from crawler.web.local_product import create_local_product

        generation_config = None
        if args.generation_config:
            from crawler.web.grounded_provider import GroundedModelConfig

            generation_config = GroundedModelConfig.read(args.generation_config)
        app = create_local_product(
            args.manifest,
            args.research_dir,
            technical=args.technical_validation,
            generation_config=generation_config,
        )
    else:
        app = create_app(
            create_bavaria_service(args.manifest, source_presentation=args.source_presentation)
        )
    address = f"http://127.0.0.1:{args.port}"
    print("Lokale Research Preview: " + address)
    if not args.no_browser:

        def ready():
            import time

            for _ in range(120):
                try:
                    with urllib.request.urlopen(address + "/api/status", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(address)
                            return
                except OSError:
                    time.sleep(0.5)

        threading.Thread(target=ready, daemon=True).start()
    pidfile.write_text(
        json.dumps(
            {"pid": os.getpid(), "port": args.port, "manifest": str(args.manifest.resolve())}
        )
    )
    pidfile.chmod(0o600)

    def exit_after_shutdown(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, exit_after_shutdown)
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            loop="asyncio",
            http="h11",
            ws="none",
            log_level="warning",
        )
    finally:
        if pidfile.exists() and json.loads(pidfile.read_text()).get("pid") == os.getpid():
            pidfile.unlink()
        lock.close()


if __name__ == "__main__":
    main()
