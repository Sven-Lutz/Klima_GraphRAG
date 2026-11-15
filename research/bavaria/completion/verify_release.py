"""Verify the compact research release without network, inference or Gold promotion."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for item in manifest["files"]:
        path = (root / item["path"]).resolve(strict=True)
        if root not in path.parents:
            raise ValueError("Release path escapes root")
        if path.stat().st_size != item["bytes"]:
            raise ValueError(f"Size mismatch: {path.name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Hash mismatch: {path.name}")
    return {
        "verified_files": len(manifest["files"]),
        "network_calls": 0,
        "model_calls": 0,
        "independent_gold_released": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release", default="research/bavaria/completion/release")
    print(json.dumps(verify(p.parse_args().release), indent=2))
