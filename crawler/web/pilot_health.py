"""Authenticated internal liveness/readiness probe; prints no identities or credentials."""

import json
import os
import urllib.request
from pathlib import Path


def main():
    user = json.loads(Path(os.environ["PILOT_USERS_FILE"]).read_text())[0]
    request = urllib.request.Request(
        "http://127.0.0.1:8080/api/pilot-health",
        headers={
            "Host": os.environ["PILOT_ORIGIN"].removeprefix("https://"),
            "X-Pilot-User": user,
            "X-Pilot-Gateway": Path(os.environ["PILOT_GATEWAY_TOKEN_FILE"]).read_text().strip(),
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        if json.load(response)["status"] != "ready":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
