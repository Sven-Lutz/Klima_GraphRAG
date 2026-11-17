
"""Combine independently verified policy events with existing benchmark answers.

Does not change the original benchmark, model, databases, or reference labels.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from evaluation.policy_event_ledger import (
    OUT, decide, load_packets, validate
)


def read_jsonl(path):
    rows = {}
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                case = record["benchmark_id"]
            except (ValueError, KeyError) as exc:
                raise ValueError(
                    f"Invalid benchmark line {number}"
                ) from exc
            rows[case] = record
    if not rows:
        raise ValueError("No benchmark records found")
    return rows


def reviewed_by_case(packets, ledger_path):
    events = json.loads(
        ledger_path.read_text(encoding="utf-8")
    )
    if not isinstance(events, list):
        raise ValueError("Ledger must be a JSON list")

    result = {case: [] for case in packets}

    for event in events:
        if event.get("verification") != "verified":
            continue

        case = event.get("case")
        if case not in packets:
            raise ValueError("Unknown verified case: " + str(case))

        checked = validate(event, packets[case])
        if checked is not None:
            result[case].append(checked)

    return result


def merge(record, packet, events, ledger_hash):
    case = record["benchmark_id"]

    if record.get("municipality_id") != packet["municipality_id"]:
        raise ValueError("Municipality mismatch: " + case)

    normalize = lambda text: " ".join(
        str(text).split()
    ).casefold()

    if normalize(record.get("question", "")) != normalize(
        packet["question"]
    ):
        raise ValueError("Question mismatch: " + case)

    config = record.get("run_config") or {}
    if config.get("snapshot_id") != packet["snapshot_id"]:
        raise ValueError("Snapshot mismatch: " + case)

    model_label = record["predicted_label"]
    verified_label, reason, supporting = decide(events)

    # Contradictory verified claims must be reviewed.
    if verified_label == "Yes" and any(
        event.get("event") == "explicit_none"
        and event.get("coverage_scope") == "entire_family"
        for event in events
    ):
        verified_label = "Unknown"
        reason = "Conflicting policy events"
        supporting = []

    use_ledger = verified_label in ("Yes", "No")
    final_label = verified_label if use_ledger else model_label

    evidence = []
    if use_ledger:
        for event in supporting:
            evidence.append({
                "scheme_id": event["scheme_id"],
                "event": event["event"],
                "source_url": event["source_url"],
                "quote": event["quote"],
                "evidence_id": event["evidence_id"],
                "chunk_id": event["chunk_id"],
                "source_text_sha256": event["source_text_sha256"],
                "effective_from": event["effective_from"],
                "as_of_checked": event["as_of_checked"]
            })

    if use_ledger:
        answer = (
            ("YES" if final_label == "Yes" else "NO")
            + ": " + reason + "; "
            + "; ".join(e["scheme_id"] for e in supporting)
        )
    else:
        answer = (
            record.get("answer_result") or {}
        ).get("answer_text", "")

    original_hash = hashlib.sha256(
        json.dumps(
            record, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()

    return {
        "benchmark_id": case,
        "municipality": packet["municipality"],
        "question": packet["question"],
        "as_of": packet["as_of"],
        "source": (
            "verified_event_ledger"
            if use_ledger else "original_llm"
        ),
        "original_prediction": model_label,
        "final_prediction": final_label,
        "answer_text": answer,
        "decision_reason": (
            reason if use_ledger
            else "Original model answer preserved"
        ),
        "verified_citations": evidence,
        "ledger_sha256": ledger_hash,
        "original_record_sha256": original_hash,
        "original_config_signature": record.get(
            "config_signature"
        ),
        "original_snapshot_id": config["snapshot_id"],
        "gold_label_for_scoring_only": record.get(
            "human_label", ""
        )
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path(
            "evaluation/mpb012_before_integration.jsonl"
        )
    )
    parser.add_argument(
        "--output-prefix", type=Path,
        default=Path("evaluation/mpb012_hybrid_v1")
    )
    parser.add_argument("--case")
    args = parser.parse_args()

    input_path = args.input.resolve()
    ledger = OUT / "verified_events.json"

    if not ledger.is_file():
        parser.error("Verified-event ledger is missing")

    packets = load_packets()
    original = read_jsonl(input_path)
    reviewed = reviewed_by_case(packets, ledger)
    ledger_hash = hashlib.sha256(
        ledger.read_bytes()
    ).hexdigest()

    results = []
    for case, record in original.items():
        if args.case and args.case != case:
            continue

        if case not in packets:
            raise ValueError("Unknown case: " + case)

        results.append(
            merge(
                record, packets[case],
                reviewed[case], ledger_hash
            )
        )

    if not results:
        raise ValueError("No matching benchmark records")

    output = Path(
        str(args.output_prefix.resolve()) + ".jsonl"
    )
    summary = Path(
        str(args.output_prefix.resolve()) + "_summary.csv"
    )

    if output == input_path:
        raise ValueError("Cannot overwrite original results")

    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in results
        ),
        encoding="utf-8"
    )

    fields = [
        "benchmark_id", "source",
        "original_prediction", "final_prediction",
        "gold_label_for_scoring_only", "as_of",
        "decision_reason"
    ]

    with summary.open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields,
            extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(results)

    old_matches = sum(
        r["original_prediction"] ==
        r["gold_label_for_scoring_only"]
        for r in results
    )
    new_matches = sum(
        r["final_prediction"] ==
        r["gold_label_for_scoring_only"]
        for r in results
    )
    changed = [
        r for r in results
        if r["original_prediction"] != r["final_prediction"]
    ]

    print(
        "CASES:", len(results),
        "| VERIFIED-LEDGER ROUTES:",
        sum(
            r["source"] == "verified_event_ledger"
            for r in results
        )
    )
    print(
        "PREDICTIONS CHANGED:", len(changed),
        "| LABEL MATCHES:",
        old_matches, "->", new_matches,
        "/", len(results)
    )

    for r in changed:
        print(
            r["benchmark_id"] + ":",
            r["original_prediction"], "->",
            r["final_prediction"],
            "(" + r["source"] + ")"
        )

    print("OUTPUT:", output)
    print("SUMMARY:", summary)
    print(
        "Assisted hybrid matches are not "
        "standalone LLM accuracy."
    )


if __name__ == "__main__":
    main()
