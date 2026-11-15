"""Validate provisional source anchors, render a review packet, import supplied decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from research.offline_evaluation.contracts import ValidationError, canonical, json_bytes, require

ROOT = Path(__file__).resolve().parents[3]
EXPORT = ROOT / "research/experiments/e2_v11_acquisition_20261024/export"
SCHEMA = "klima-review-candidates/1"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"Missing {name}")


def read_rows(path):
    return [json_bytes(line) for line in Path(path).read_bytes().splitlines() if line.strip()]


def validate(
    path, corpus=EXPORT / "corpus.jsonl", catalog=EXPORT / "catalog.jsonl", raw_dir=EXPORT / "raw"
):
    """Check original identities and literal spans, never semantic truth or human work."""
    payload = Path(path).read_bytes()
    package = json_bytes(payload)
    require(
        isinstance(package, dict) and package.get("schema_version") == SCHEMA,
        "Unsupported candidate schema",
    )
    require(isinstance(package.get("candidates"), list) and package["candidates"], "No candidates")
    original_units = read_rows(corpus)
    units = {row["unit_id"]: row for row in original_units}
    require(len(units) == len(original_units), "Duplicate canonical unit")
    versions = {(r["url"], r["body_sha256"]) for r in read_rows(catalog)}
    ids = set()
    hashes = {}
    for item in package["candidates"]:
        for key in ("id", "task_family", "question", "proposed_answer", "rationale"):
            text(item.get(key), key)
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", item["id"]),
            "Unsafe candidate ID: only letters, digits, underscore and hyphen allowed",
        )
        require(item["id"] not in ids, "Duplicate candidate ID")
        ids.add(item["id"])
        require(
            item.get("annotation_status") == "PROVISIONAL_AGENT_SOURCE_CHECKED",
            "Candidates must remain provisional, not human-adjudicated",
        )
        require(
            item.get("answerability")
            in {"FULL", "PARTIAL", "UNSUPPORTED_IN_SCOPE", "CONFLICTED", "UNASSESSED"},
            "Invalid proposed answerability",
        )
        require(
            item.get("review_priority") in {"HIGH", "NORMAL", "LOW", 1, 2, 3}
            and type(item.get("review_priority")) in (str, int),
            "Invalid review priority",
        )
        municipalities = item.get("municipalities")
        require(
            isinstance(municipalities, list)
            and municipalities
            and all(isinstance(x, str) and re.fullmatch(r"[0-9]{8}", x) for x in municipalities)
            and len(municipalities) == len(set(municipalities)),
            "Invalid target municipalities",
        )
        require(
            isinstance(item.get("limitations"), list)
            and all(isinstance(x, str) for x in item["limitations"]),
            "Explicit limitations required",
        )
        sources = item.get("source_versions")
        require(isinstance(sources, list) and sources, "Explicit source scope required")
        scope = set()
        for source in sources:
            key = (source.get("source_url"), source.get("raw_sha256"))
            require(key in versions, "Candidate source version absent from frozen catalog")
            require(key not in scope, "Duplicate candidate source version")
            scope.add(key)
            require(sha((Path(raw_dir) / key[1]).read_bytes()) == key[1], "Raw body hash mismatch")
        require(isinstance(item.get("evidence"), list), "Evidence list required")
        if item["answerability"] in {"FULL", "PARTIAL", "CONFLICTED"}:
            require(item["evidence"], "Supported candidate needs source anchors")
        anchors = set()
        for anchor in item["evidence"]:
            require(anchor.get("unit_id") in units, "Unknown canonical unit")
            unit = units[anchor["unit_id"]]
            key = (anchor.get("source_url"), anchor.get("raw_sha256"))
            require(
                key in scope and key[0] == unit["source_url"] and key[1] in unit["raw_hashes"],
                "Evidence source identity mismatch",
            )
            start, end = anchor.get("start_char"), anchor.get("end_char")
            require(
                type(start) is int and type(end) is int and 0 <= start < end <= len(unit["text"]),
                "Invalid Unicode source offsets",
            )
            require(unit["text"][start:end] == anchor.get("quote"), "Source quote mismatch")
            require(
                "page_ref" in anchor and anchor["page_ref"] == unit.get("page_ref"),
                "Source page mismatch",
            )
            identity = (anchor["unit_id"], start, end, key)
            require(identity not in anchors, "Duplicate evidence anchor")
            anchors.add(identity)
        hashes[item["id"]] = sha(canonical(item))
    require(Path(path).read_bytes() == payload, "Candidate bytes changed during validation")
    return (
        package,
        {
            "schema_version": SCHEMA,
            "candidate_file_sha256": sha(payload),
            "candidate_sha256": hashes,
            "corpus_sha256": sha(Path(corpus).read_bytes()),
            "catalog_sha256": sha(Path(catalog).read_bytes()),
            "validation": "LITERAL_SOURCE_INTEGRITY_NOT_SEMANTIC_ADJUDICATION",
        },
        original_units,
    )


def render(
    path,
    out,
    corpus=EXPORT / "corpus.jsonl",
    catalog=EXPORT / "catalog.jsonl",
    raw_dir=EXPORT / "raw",
):
    package, manifest, units = validate(path, corpus, catalog, raw_dir)
    out = Path(out)
    require(not out.exists() and not out.is_symlink(), "Use a fresh review output directory")
    questions = [
        "# Quellenprüfung — zuerst ohne Antwortvorschläge\n",
        "Vorläufige, agentisch vorbereitete Aufgaben. Keine abgeschlossene menschliche Prüfung. "
        "Die Quellenauswahl ist sichtbar; dies zertifiziert keine vollständige Blindheit.\n",
    ]
    references = [
        "# Antwortvorschläge — erst nach eigener Quellenprüfung öffnen\n",
        "Alle Einträge bleiben PROVISIONAL_AGENT_SOURCE_CHECKED.\n",
    ]
    source_types = {
        (row["url"], row["body_sha256"]): str(row.get("source_type", "")).lower()
        for row in read_rows(catalog)
    }
    original_aliases = {}
    item_views = {}
    priorities = {"HIGH": 1, "NORMAL": 2, "LOW": 3, 1: 1, 2: 2, 3: 3}
    ordered = sorted(
        package["candidates"], key=lambda item: (priorities[item["review_priority"]], item["id"])
    )
    index = [
        "# Menschliche Quellenprüfung — Einstieg\n",
        "Status: REVIEWER_AVAILABLE_PENDING_REVIEW. Noch keine menschlichen Urteile.\n",
        "Bitte je Aufgabe zuerst **Frage und Originalquellen** lesen und eigene Notizen machen; "
        "erst anschließend den getrennten Antwortvorschlag öffnen. Die Reihenfolge folgt "
        "der gezielten Prüfpriorität, nicht einer Zufallsstichprobe. Bekannte E2-Exposition "
        "und agentische Quellenvorauswahl bleiben offengelegt.\n",
        "| Priorität | Aufgabe | Zuerst prüfen | Danach abgleichen |\n",
        "|---|---|---|---|\n",
    ]
    for item in ordered:
        own_questions = [
            f"# {item['id']} — Frage und Quellen\n",
            item["question"] + "\n",
            "Zielkommunen: " + ", ".join(item["municipalities"]) + "\n",
            "Die folgenden kanonischen Texte sind abgeleitete Parserausgaben, "
            "keine vollständige Wiedergabe des Originals. Parser können Überschriften, "
            "Listen, Linkbezüge oder andere Inhalte verlieren. Bitte die gespeicherte "
            "Originalfassung öffnen und Kontext/Geltung dort prüfen.\n",
        ]
        for source in item["source_versions"]:
            key = (source["source_url"], source["raw_sha256"])
            extension = {"html": ".html", "pdf": ".pdf"}.get(source_types.get(key), ".bin")
            alias = out.resolve() / "originals" / (key[1] + extension)
            original_aliases[alias] = Path(raw_dir).resolve() / key[1]
            own_questions += [
                f"### Quelle: {key[0]}\n",
                f"SHA-256: `{key[1]}`\n",
                f"Gespeicherte Originalfassung öffnen: [{key[1]}{extension}]({alias})\n",
                f"Gespeichertes Original: [{key[1]}]({Path(raw_dir).resolve() / key[1]})\n",
            ]
            for unit in units:
                if unit["source_url"] == key[0] and key[1] in unit["raw_hashes"]:
                    own_questions += [
                        f"Unit `{unit['unit_id']}`; Seite `{unit.get('page_ref')}`\n",
                        unit["text"] + "\n",
                    ]
        own_references = [
            f"# {item['id']} — vorläufiger Antwortvorschlag\n",
            f"Candidate-SHA-256: `{manifest['candidate_sha256'][item['id']]}`\n",
            item["proposed_answer"] + "\n",
            "Vorgeschlagene Beantwortbarkeit: " + item["answerability"] + "\n",
            "Begründung: " + item["rationale"] + "\n",
            "Einschränkungen: " + "; ".join(item["limitations"]) + "\n",
        ]
        for anchor in item["evidence"]:
            own_references += [
                f"- `{anchor['unit_id']}` [{anchor['start_char']},{anchor['end_char']}): "
                + json.dumps(anchor["quote"], ensure_ascii=False)
                + "\n"
            ]
        item_views[item["id"]] = ("\n".join(own_questions), "\n".join(own_references))
        questions += own_questions
        references += own_references
        index.append(
            f"| {priorities[item['review_priority']]} | {item['id']} | "
            f"[Frage und Originalquellen](questions/{item['id']}.md) | "
            f"[Vorläufige Referenzantwort](answers/{item['id']}.md) |\n"
        )
    out.mkdir(parents=True)
    (out / "questions").mkdir()
    (out / "answers").mkdir()
    (out / "originals").mkdir()
    for alias, raw_path in original_aliases.items():
        # Only create new aliases in the exclusively claimed output directory. Never
        # copy raw bytes or open an alias for writing; targets remain frozen inputs.
        alias.symlink_to(os.path.relpath(raw_path, alias.parent))
    for cid, (question_text, answer_text) in item_views.items():
        (out / "questions" / f"{cid}.md").write_text(question_text)
        (out / "answers" / f"{cid}.md").write_text(answer_text)
    # Each table row already ends in a newline; extra blank lines break GFM tables.
    (out / "REVIEW_START.md").write_text("\n".join(index[:3]) + "\n" + "".join(index[3:]))
    (out / "01_questions_and_sources.md").write_text("\n".join(questions))
    (out / "02_proposed_references.md").write_text("\n".join(references))
    (out / "review_manifest.json").write_bytes(canonical(manifest) + b"\n")
    return manifest


def import_decision(
    candidates,
    decision_file,
    log,
    expected_reviewer_id,
    corpus=EXPORT / "corpus.jsonl",
    catalog=EXPORT / "catalog.jsonl",
    raw_dir=EXPORT / "raw",
):
    """Append only a supplied reviewer record; never populate a reviewer decision."""
    _, manifest, _ = validate(candidates, corpus, catalog, raw_dir)
    supplied = Path(decision_file).read_bytes()
    record = json_bytes(supplied)
    require(isinstance(record, dict), "Decision must be a JSON object")
    require(
        record.get("origin") == "HUMAN_REVIEWER_SUPPLIED", "Explicit human-supplied origin required"
    )
    require(
        record.get("reviewer_id") == expected_reviewer_id and bool(expected_reviewer_id.strip()),
        "Reviewer does not match explicitly expected identity",
    )
    for key in ("candidate_id", "reasoning", "reviewed_at"):
        text(record.get(key), key)
    cid = record["candidate_id"]
    require(cid in manifest["candidate_sha256"], "Unknown reviewed candidate")
    require(
        record.get("candidate_sha256") == manifest["candidate_sha256"][cid],
        "Decision is not linked to current candidate content",
    )
    require(
        record.get("decision") in {"ACCEPT", "REVISE", "REJECT", "UNSURE"}, "Invalid human decision"
    )
    require(type(record.get("checked_sources")) is bool, "Explicit checked_sources required")
    require(
        record["decision"] != "ACCEPT" or record["checked_sources"],
        "Accept requires actual declared source checking",
    )
    try:
        reviewed = datetime.fromisoformat(record["reviewed_at"])
    except ValueError as exc:
        raise ValidationError("Invalid reviewed_at") from exc
    require(reviewed.tzinfo is not None, "reviewed_at needs explicit timezone")
    from .review_log import append_decisions

    receipt = append_decisions(Path(log), [record], manifest, sha(supplied))
    return receipt["results"][0]["event"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--corpus", type=Path, default=EXPORT / "corpus.jsonl")
    parser.add_argument("--catalog", type=Path, default=EXPORT / "catalog.jsonl")
    parser.add_argument("--raw-dir", type=Path, default=EXPORT / "raw")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("validate")
    renderer = sub.add_parser("render")
    renderer.add_argument("--out", required=True, type=Path)
    importer = sub.add_parser("import-decision")
    importer.add_argument("--decision-file", required=True, type=Path)
    importer.add_argument("--log", required=True, type=Path)
    importer.add_argument("--expected-reviewer-id", required=True)
    args = parser.parse_args()
    common = {"corpus": args.corpus, "catalog": args.catalog, "raw_dir": args.raw_dir}
    if args.operation == "render":
        result = render(args.candidates, args.out, **common)
    elif args.operation == "import-decision":
        result = import_decision(
            args.candidates, args.decision_file, args.log, args.expected_reviewer_id, **common
        )
    else:
        result = validate(args.candidates, **common)[1]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
