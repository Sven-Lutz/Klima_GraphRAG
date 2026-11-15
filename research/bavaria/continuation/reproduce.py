"""Verify the small research release and optionally its local original ledgers; no inference."""

import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify(release, local=False):
    release = Path(release)
    manifest = json.loads((release / "MANIFEST.json").read_text())
    for name, expected in manifest["files_sha256"].items():
        if sha(release / name) != expected:
            raise ValueError("Release integrity mismatch: " + name)
    if local:
        for name, expected in manifest["local_databases_sha256"].items():
            if sha(name) != expected:
                raise ValueError("Local snapshot mismatch: " + name)
    return {
        "status": "VERIFIED",
        "release_files": len(manifest["files_sha256"]),
        "local_databases_checked": local,
        "model_or_network_calls": 0,
        "independent_human_validation": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="research/bavaria/continuation/release")
    parser.add_argument("--verify-local", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.release, args.verify_local), indent=2))
