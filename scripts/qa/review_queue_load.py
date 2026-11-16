"""Bounded synthetic queue load check; no judgments, real reviewers or quality labels."""

import argparse
import ast
import hashlib
import json
import subprocess
import time
from pathlib import Path

from crawler.web.review_protocol import ReviewProtocol

TEXT = "Own synthetic queue passage. No municipal source or human judgment."
SHA = hashlib.sha256(TEXT.encode()).hexdigest()


def resolve(source):
    if source != {"sha256": SHA, "quote": TEXT}:
        raise ValueError("Only own synthetic source permitted")
    return dict(source, unit_id="synthetic-load-unit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--measure-existing", action="store_true")
    parser.add_argument("--compare-v1", action="store_true")
    args = parser.parse_args()
    if args.measure_existing:
        if not (args.directory / "protocol.sqlite").is_file():
            raise ValueError("Explicit existing technical database required")
    elif args.directory.exists():
        raise ValueError("New isolated output directory required")
    started = time.perf_counter()
    protocol = ReviewProtocol(
        args.directory, "queue-load-fixture-v1", technical=True, source_resolver=resolve
    )
    if not args.measure_existing:
        for name, role in [
            ("load-a", "reviewer"),
            ("load-b", "reviewer"),
            ("load-judge", "adjudicator"),
        ]:
            protocol.create_account(
                "owner",
                name,
                "synthetic-load-password-only",
                identity="SYNTHETIC " + name,
                role=role,
            )
        for start in [0, 200]:
            protocol.import_candidates(
                "owner",
                [
                    {
                        "kind": "claim",
                        "key": "synthetic-load-" + str(i),
                        "payload": {"label": "Synthetic case " + str(i)},
                        "sources": [{"sha256": SHA, "quote": TEXT}],
                    }
                    for i in range(start, start + 200)
                ],
            )
        for oid in protocol.objects():
            protocol.assign("owner", oid, ["load-a", "load-b"], "load-judge")
    setup = time.perf_counter() - started
    database = args.directory / "protocol.sqlite"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    legacy_seconds = None
    if args.compare_v1:
        # Explicit reviewed public V1 implementation, not arbitrary repository code.
        baseline = "0483eef9d8bb9c37559d8dcee0e0b06449d9a394"
        source = subprocess.check_output(
            ["git", "show", baseline + ":crawler/web/review_protocol.py"], text=True
        )
        tree = ast.parse(source)
        cls = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReviewProtocol"
        )
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "queue")
        import crawler.web.review_protocol as current

        namespace = dict(vars(current))
        exec(
            compile(ast.Module(body=[method], type_ignores=[]), "reviewed-v1-queue", "exec"),
            namespace,
        )
        started = time.perf_counter()
        legacy = namespace["queue"](protocol, "load-a", limit=50)
        legacy_seconds = time.perf_counter() - started
    started = time.perf_counter()
    queue = protocol.queue("load-a", limit=50)
    seconds = time.perf_counter() - started
    assert queue["total"] == 400 and len(queue["items"]) == 50
    assert all(row["state"] == "PENDING" for row in queue["items"])
    assert protocol._events("protocol_review") == []
    assert protocol._events("protocol_adjudication") == []
    assert protocol.store.audit()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    if args.compare_v1:
        assert legacy == queue
    print(
        json.dumps(
            {
                "schema": "SYNTHETIC_QUEUE_LOAD_V1",
                "technical": True,
                "human_gold": False,
                "judgments": 0,
                "objects": 400,
                "page": 50,
                "setup_seconds": setup,
                "queue_seconds": seconds,
                "v1_queue_seconds_same_database": legacy_seconds,
                "database_sha256_before_after": before,
                "protocol_sha256": hashlib.sha256(
                    Path("crawler/web/review_protocol.py").read_bytes()
                ).hexdigest(),
                "scope": "repeated technical units, not documents or reviewer time",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
