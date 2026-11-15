"""Execute fresh engineering questions, preserving outputs for explicit content review."""

import argparse
import json
from pathlib import Path

from crawler.core.retrieval import RetrievalMode
from crawler.web.bavaria import create_bavaria_service
from crawler.web.local_index import LocalIndex
from crawler.web.product_research import ResearchProduct
from crawler.web.semantic_index import SemanticIndex
from crawler.web.service import QueryOptions


def run(manifest, directory, challenge, output, method):
    output.mkdir(parents=True, exist_ok=False)
    service = create_bavaria_service(manifest)
    service.workspace.discovery_index = LocalIndex(service.workspace, directory / "terms-v1.sqlite")
    semantic = SemanticIndex(service.workspace, directory / "semantic-v1")
    product = ResearchProduct(service, output / "technical-research", semantic)
    cases = json.loads(challenge.read_text())["cases"]
    previous, rows = {}, []
    for case in cases:
        mode = RetrievalMode.GRAPH if case.get("method") == "graph" else RetrievalMode(method)
        options = QueryOptions(case["question"], mode, 16, 16, 48000, True)
        try:
            answer = product.ask(
                options, "ISOLATED_ENGINEERING_QA", previous.get(case.get("followup_of"))
            )
            if answer.get("result_id"):
                previous[case["case_id"]] = answer["result_id"]
            rows.append(
                dict(
                    case_id=case["case_id"],
                    question=case["question"],
                    method=mode.value,
                    status="EXECUTED_CONTENT_REVIEW_REQUIRED",
                    response=answer,
                )
            )
        except Exception as exc:
            rows.append(
                dict(
                    case_id=case["case_id"],
                    question=case["question"],
                    method=mode.value,
                    status="FAILED",
                    error_class=type(exc).__name__,
                    error=str(exc),
                )
            )
        (output / "RESULTS.json").write_text(
            json.dumps(dict(scientific_gold=False, rows=rows), ensure_ascii=False, indent=2) + "\n"
        )
    print(
        json.dumps(
            dict(
                cases=len(rows),
                executed=sum(r["status"].startswith("EXECUTED") for r in rows),
                failed=sum(r["status"] == "FAILED" for r in rows),
                content_review="REQUIRED",
            )
        )
    )
    service.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--manifest",
        type=Path,
        default=Path("research/bavaria/completion/release/federated-workspace.json"),
    )
    p.add_argument("--directory", type=Path, default=Path("generated/local-research"))
    p.add_argument("--challenge", type=Path, default=Path("generated/product-qa-challenge-v1.json"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--method", default="lexical_expanded", choices=["lexical_expanded", "semantic", "hybrid"]
    )
    a = p.parse_args()
    run(a.manifest, a.directory, a.challenge, a.output, a.method)
