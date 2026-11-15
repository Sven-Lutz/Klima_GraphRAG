"""Serial real-product answer execution, including the bounded async job transport.

Execution success never substitutes for explicit content/citation review.
"""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


def run(base, challenge, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = json.loads(challenge.read_text())["cases"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    rows = []
    for case in cases:
        start = time.monotonic()
        record = dict(case_id=case["case_id"], question=case["question"])
        try:
            request = urllib.request.Request(
                base.rstrip("/") + "/api/research/ask",
                data=json.dumps(
                    dict(
                        question=case["question"],
                        mode=case.get("method", "hybrid"),
                        top_k=16,
                        max_evidence_items=8,
                        max_evidence_characters=8000,
                        generate_answer=True,
                    )
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=20) as response:
                result = json.load(response)
            pending_seen = result.get("status") == "RESEARCH_PENDING"
            while result.get("status") == "RESEARCH_PENDING":
                if time.monotonic() - start > 180:
                    raise TimeoutError("Bounded local research job did not complete")
                job_id = result["job_id"]
                if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
                    raise ValueError("Unexpected local job identity")
                time.sleep(2)
                with opener.open(
                    base.rstrip("/") + "/api/research/jobs/" + job_id, timeout=17
                ) as response:
                    result = json.load(response)
            record.update(
                response=result,
                pending_transport=pending_seen,
                status="EXECUTED_CONTENT_REVIEW_REQUIRED",
            )
        except Exception as exc:
            record.update(status="FAILED", error_class=type(exc).__name__, error=str(exc))
        record["elapsed_seconds"] = time.monotonic() - start
        rows.append(record)
        (output / "RESULTS.json").write_text(
            json.dumps(
                dict(
                    schema="http-product-content-challenge-v1",
                    challenge_sha256=hashlib.sha256(challenge.read_bytes()).hexdigest(),
                    scientific_gold=False,
                    holdout_accessed=False,
                    rows=rows,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        print(case["case_id"], record["status"], round(record["elapsed_seconds"], 2), flush=True)
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8813")
    p.add_argument("--challenge", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    run(**vars(p.parse_args()))
