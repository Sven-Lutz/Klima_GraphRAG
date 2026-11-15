"""Offline-only validation and compact DS007 result export. Never dispatches HTTP."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .review import REVIEW, TASK_REVIEW

ROOT = Path(__file__).resolve().parents[3]
ARMS = ("burggen-heuristic", "burggen-structured",
        "nuernberg-heuristic", "nuernberg-structured")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def analyze(base):
    runs, evidence, tasks, conflicts, manifests = [], [], [], [], []
    snapshots = {}
    for arm in ARMS:
        folder = base / arm
        run, transport = read(folder / "run.json"), read(folder / "transport.json")
        docs, segments = read(folder / "documents.json"), read(folder / "segments.json")
        events = transport["requests"]
        assert run["requests"] == len(events) == 20, arm
        assert run["bytes"] == sum(e["bytes"] for e in events), arm
        assert run["request_budget"] == 20 and not run["errors"], arm
        assert [e["number"] for e in events] == list(range(1, 21)), arm
        assert run["runner_sha256"] == digest((ROOT / "research/experiments/ds007/pilot.py").read_bytes())
        for path, expected in run["input_hashes"].items():
            assert digest((ROOT / path).read_bytes()) == expected, path
        for event in events:
            assert event["ags"] == run["ags"]
            if "body_path" in event:
                body = (folder / event["body_path"]).read_bytes()
                assert digest(body) == event["sha256"], event["url"]
                assert len(body) == event["bytes"]
        successful = {e["url"]: e for e in events
                      if e["status"] == 200 and e["complete"] and not e["error"]}
        snapshots[arm] = {u: e["sha256"] for u, e in successful.items()}
        source_urls = {d["url_final"] for d in docs if d["status_code"] == 200}
        assert source_urls <= successful.keys()
        # Scope is the collection's AGS, not inferred statement jurisdiction.
        for doc in docs:
            url = doc["url_final"]
            if "/2_G_" in url and "ris.kommune-aktiv.de" in url:
                heading = next(s for s in segments if s["url_final"] == url
                               and s["order_index"] == 6)
                assert heading["text"].endswith(" - Bernbeuren")
                conflicts.append(dict(arm=arm, url=url, acquisition_ags=run["ags"],
                    source_municipality="Bernbeuren", locator="segment order_index=6",
                    heading=heading["text"], source_sha256=successful[url]["sha256"],
                    status="scope_mismatch_not_an_asserted_false_claim"))
        selected = [r for r in REVIEW if r["arm"] == arm]
        for record in selected:
            matches = [s for s in segments if s["url_final"].endswith(record["suffix"])
                       and s["order_index"] == record["segment"]]
            assert len(matches) == 1, record["id"]
            segment = matches[0]
            assert record["quote"] in segment["text"], record["id"]
            event = successful[segment["url_final"]]
            evidence.append(dict(record, url=segment["url_final"], ags=run["ags"],
                page_ref=segment["page_ref"], heading_context=segment["heading_context"],
                quote_offset=segment["text"].index(record["quote"]),
                segment_text_sha256=digest(segment["text"].encode()),
                source_sha256=event["sha256"], response_number=event["number"],
                response_body_path=f"{arm}/{event['body_path']}", parent=event["parent"],
                fetched_at=event["timestamp_utc"], annotation="assistant_reviewed_provisional_not_gold"))
        for task_id, (status, ids, note) in TASK_REVIEW[arm].items():
            tasks.append(dict(arm=arm, task_id=task_id, status=status, evidence_ids=ids,
                              note=note, strict_cutoff_status="unverified",
                              gold_status="not_annotated"))
        assert set(TASK_REVIEW[arm]) == set(run["task_ids"])
        with sqlite3.connect(f"file:{folder / 'pilot.sqlite'}?mode=ro", uri=True) as con:
            chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        supported = sum(v[0] == "snapshot_supported" for v in TASK_REVIEW[arm].values())
        runs.append(dict(arm=arm, **run,
            unique_source_urls=len(source_urls),
            unique_wire_document_bodies=len({successful[u]["sha256"] for u in source_urls}),
            documents_original_domain=sum(urlsplit(u).hostname in run["original_allowlist"] for u in source_urls),
            linked_external_source_urls=sum(urlsplit(u).hostname not in run["original_allowlist"] for u in source_urls),
            response_errors=dict(Counter(e["error"] for e in events if e["error"])),
            blocked_dispatches=dict(Counter(e["reason"] for e in transport["blocked"])),
            admitted_domains=sorted({d["host"] for d in transport["domains"] if d["admitted"]}),
            original_allowlist_blocked_acquired_urls=sorted(u for u in source_urls
                if urlsplit(u).hostname not in run["original_allowlist"]),
            sitemap_requests=sum(e["purpose"] == "sitemap" for e in events),
            reviewed_evidence_records=len(selected),
            task_support_records=sum(r["kind"] == "task_support" for r in selected),
            snapshot_supported_tasks=supported, task_denominator=4,
            snapshot_task_coverage=supported/4, strict_cutoff_task_coverage=None,
            scope_conflict_documents=sum(c["arm"] == arm for c in conflicts),
            chunks=chunks))
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                manifests.append(dict(path=str(path.relative_to(base)), bytes=path.stat().st_size,
                                      sha256=digest(path.read_bytes())))
    drift = []
    for a, b in zip(ARMS[::2], ARMS[1::2]):
        assert runs[ARMS.index(a)]["seed"] == runs[ARMS.index(b)]["seed"]
        assert runs[ARMS.index(a)]["input_hashes"] == runs[ARMS.index(b)]["input_hashes"]
        for url in sorted(snapshots[a].keys() & snapshots[b].keys()):
            drift.append(dict(arms=[a, b], url=url, identical=snapshots[a][url] == snapshots[b][url],
                              sha256_a=snapshots[a][url], sha256_b=snapshots[b][url]))
    return dict(schema_version="ds007-v1", classification="generated_reproducible_local_research",
                judgment="posthoc_assistant_review_not_independent_gold",
                review_scope="nonexhaustive_traceable_sample; counts_are_lower_bounds",
                runs=runs, evidence=evidence, tasks=tasks, conflicts=conflicts,
                shared_response_drift=drift, artifact_manifest=manifests)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=ROOT / "generated/ds007")
    args = parser.parse_args()
    result = analyze(args.artifacts)
    (args.artifacts / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    fields = ["arm", "requests", "bytes", "wall_seconds", "acquired_documents",
              "unique_source_urls", "unique_wire_document_bodies", "task_support_records",
              "snapshot_supported_tasks", "task_denominator", "scope_conflict_documents"]
    with (args.artifacts / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["runs"])
    print(json.dumps({r["arm"]: {k: r[k] for k in fields[1:]} for r in result["runs"]}, indent=2))


if __name__ == "__main__":
    main()
