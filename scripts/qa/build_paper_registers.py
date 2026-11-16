"""Build bounded public paper registers from explicit reviewed project artifacts.

Never searches private originals or manufactures labels. Existing private receipts
are represented by an optional SHA only, not by their source payloads.
"""

import csv
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "docs/paper"
CODE_COMMIT = "a0bb110"  # resolve against local Git, do not infer historical experiment commits


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def write(name, fields, rows, delimiter=","):
    with (PAPER / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=delimiter, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    # V1 registers are now explicitly frozen. Do not silently rebind historical claims.
    header = (PAPER / "CLAIM_EVIDENCE_REGISTER.csv").read_text().splitlines()[0]
    if "source_commit" in header:
        raise SystemExit(
            "V1 registers frozen; use refresh_v2_registers.py for current V2 artifacts"
        )
    code = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", CODE_COMMIT], text=True
    ).strip()
    definitions = [
        (
            "P01",
            (
                "The public chat uses canonical retrieval and deterministic extractive "
                "qualification, not a configured chat LLM."
            ),
            "crawler/web/conversation.py",
            "ConversationEngine.answer",
            "Conversational-v1",
            "VERIFIED",
            "Code inspection; technical contract only",
        ),
        (
            "P02",
            "Conversation turns have immutable IDs and explicit parent version links.",
            "crawler/web/conversation.py",
            "ChatStore.save; ConversationEngine.answer",
            "Conversational-v1",
            "VERIFIED",
            "Implementation plus synthetic regression; not general security certification",
        ),
        (
            "P03",
            (
                "The review transport delegates blinded individual judgments to the "
                "existing append-only ReviewProtocol."
            ),
            "crawler/web/conversation_review.py",
            "attach_conversation_review",
            "Technical-review",
            "VERIFIED",
            "Technical tests; real human participation not established",
        ),
        (
            "P04",
            (
                "The historical retrieval report records identical SQL and graph relation "
                "sets on 38 formalized tasks."
            ),
            "docs/research/artifacts/retrieval03-report.json",
            "json:graph_sql_all_tasks_same_relations;tasks",
            "Relations03",
            "VERIFIED",
            "Faithful report reading; underlying references provisional",
        ),
        (
            "P05",
            (
                "The positive exposed subset reports known complete bundles 23/28 for "
                "context BM25 and 26/28 with the same relation information."
            ),
            "docs/research/artifacts/retrieval03-report.json",
            "json:summary_evaluation_provisional_positive",
            "Relations03",
            "PROVISIONAL",
            "Source-exposed incomplete qrels; not a graph-format effect",
        ),
        (
            "P06",
            (
                "The IE report has eight cases and 56 dependent field slots; semantic "
                "labels are not independently human validated."
            ),
            "docs/research/artifacts/ie-1.7b-summary.json",
            "json:cases;arms;independent_human_judgments",
            "Stronger-IE01",
            "VERIFIED",
            "Aggregate arithmetic, not independent factual correctness",
        ),
        (
            "P07",
            (
                "The current public synthetic DS006 control has B held_out chunk_present "
                "4/5, distinct from the historical parser result."
            ),
            "docs/research/artifacts/ds006-current-result.json",
            "json:summary.B.held_out",
            "DS006-current-public",
            "VERIFIED",
            "Synthetic mechanical fixture replay only",
        ),
        (
            "P08",
            (
                "Wrong units, wrong years and source instruction patterns are technical "
                "negative qualification cases."
            ),
            "tests/test_conversation.py",
            (
                "test_quantities_must_match_requested_dimension;test_negative_qualification_is_not_case_name_lookup"
            ),
            "Conversational-regression",
            "VERIFIED",
            "Test specification; execution receipts separate; no population accuracy",
        ),
        (
            "P09",
            (
                "This release does not establish general GraphRAG superiority or "
                "independent Human Gold."
            ),
            "RESEARCH_FINDINGS_AND_LIMITATIONS.md",
            "Limitations and per-study status",
            "All",
            "NOT_EVALUATED",
            "No independent annotation or fair confirmatory comparison",
        ),
        (
            "P10",
            "Raw originals and human-review data are outside the public snapshot.",
            "DATA_POLICY.md",
            "Code/data/license boundary",
            "Release-policy",
            "VERIFIED",
            "Publication policy plus allowlist; no legal blanket claim",
        ),
    ]
    claims = []
    for cid, sentence, source, locator, study, status, reason in definitions:
        claims.append(
            dict(
                claim_id=cid,
                sentence=sentence,
                source=source,
                sha256=sha(source),
                original_span=locator,
                study=study,
                exposure="DEVELOPMENT_EXPOSED_OR_TECHNICAL",
                confidence_basis=reason,
                status=status,
                rights="OWN_CODE_DOCUMENTATION_OR_REVIEWED_AGGREGATE",
                independent_human_validation="false",
            )
        )
    write("CLAIM_EVIDENCE_REGISTER.csv", list(claims[0]), claims)
    fields = [
        "experiment_id",
        "stage",
        "rq_hypothesis",
        "snapshot",
        "task_taxonomy",
        "methods",
        "information",
        "query_formalization",
        "generator",
        "costs",
        "n_tasks",
        "n_independent_components",
        "qrel_status",
        "result",
        "confidence_interval",
        "evidence_path",
        "limits",
        "reproduction_status",
        "code_commit",
    ]
    rows = [
        [
            "DS006-public",
            "EXISTING RESULT",
            "RQ2/RQ3;H3 motivation",
            "input_sha256 in result.json",
            "SYNTHETIC_MECHANICAL",
            "admission A/B",
            "same invented HTML fixtures",
            "known anchors",
            "none",
            "stored local timings; no paid inference",
            "15 positive fixture checks",
            "0 real municipalities",
            "synthetic oracle, not Human Gold",
            "A 7/10 and 0/5; B 10/10 and 4/5",
            "NOT_APPLICABLE",
            "docs/research/artifacts/ds006-current-result.json",
            "not municipal recall",
            "PUBLIC_SYNTHETIC_REPLAY",
            "historical code hashes in adjacent manifest",
        ],
        [
            "IE01",
            "EXISTING RESULT",
            "RQ3;H4 context",
            "original report SHA in summary",
            "8 exposed cases;56 fields",
            "rules/Qwen3 1.7B/hybrid",
            "same selected IE material",
            "fixed extraction fields",
            "IE model, not answer generation",
            "stored historical record",
            "8 cases;56 dependent slots",
            "NOT_ESTABLISHED;8 documented components",
            "provisional original review",
            "TP/FP/FN:2/1/25;16/40/11;5/5/22",
            "NOT_EVALUATED",
            "docs/research/artifacts/ie-1.7b-summary.json",
            "not population F1;missing raw inputs",
            "AGGREGATE_CHECK_ONLY",
            "NOT_KNOWN",
        ],
        [
            "REL03-format",
            "EXISTING RESULT",
            "RQ5;H5",
            "a8e5ac8c83348e6481eb44469a40d0ae78f29d26694c19e97993a67373fb6280",
            "38 formalized relation tasks",
            "SQL/graph",
            "same curated relations",
            "formal/Oracle",
            "none",
            "historical retrieval timings only",
            "38",
            "NOT_ESTABLISHED;18 dependency components",
            "provisional,exposed",
            "same relation sets on all tasks",
            "NOT_EVALUATED",
            "docs/research/artifacts/retrieval03-report.json",
            "not free query planning or format utility",
            "AGGREGATE_CHECK_ONLY",
            "NOT_KNOWN",
        ],
        [
            "REL03-info",
            "EXISTING RESULT",
            "RQ4/RQ5;H1/H5 context",
            "same REL03 snapshot",
            "28 positive exposed tasks",
            "BM25/cards/dense/RRF/SQL/graph",
            "information access differs;cards arm controlled separately",
            "text vs formal",
            "none",
            "not end-to-end generation cost",
            "28",
            "NOT_ESTABLISHED",
            "incomplete known bundles",
            "23/26/13/20/28/28 known complete bundles respectively",
            "NOT_EVALUATED",
            "docs/research/artifacts/retrieval03-report.json",
            "dependent tasks;no global graph advantage",
            "AGGREGATE_CHECK_ONLY",
            "NOT_KNOWN",
        ],
        [
            "CHAT-tech",
            "EXISTING RESULT",
            "RQ7/RQ8",
            "canonical synthetic fixture snapshot at run",
            "technical contracts,not blind questions",
            "canonical semantic/graph fixture",
            "bounded synthetic passages",
            "explicit scope and bounded routing",
            "deterministic extractive",
            "0 paid inference",
            "see actual test receipt",
            "0 independently sampled humans",
            "NO_HUMAN_GOLD",
            "see CONVERSATIONAL_RESEARCH_HANDOFF.md",
            "NOT_APPLICABLE",
            "tests/test_conversation.py",
            "no real answer accuracy/UX superiority",
            "PUBLIC_CODE_AND_FIXTURES",
            code,
        ],
    ]
    for eid, rq, methods, info in [
        ("F-retrieval", "RQ4/RQ5;H1/H5", "BM25,dense,RRF,SQL,graph", "same corpus and budgets"),
        (
            "F-generation",
            "RQ6/RQ7;H6",
            "extractive/Ollama",
            "identical evidence IDs and context tokens",
        ),
        (
            "F-information",
            "RQ5;H5",
            "BM25/cards and same-info SQL/graph",
            "separate information factor",
        ),
        ("F-routing", "RQ5/RQ7", "formal/actual planner", "separate formalization factor"),
        ("F-UX", "RQ7/RQ8", "formative paired task sessions", "same approved task/source versions"),
    ]:
        rows.append(
            [
                eid,
                "FUTURE EXPERIMENT",
                rq,
                "OWNER_APPROVED_FREEZE_REQUIRED",
                "T1-T9 as admitted",
                methods,
                info,
                "prespecification pending",
                "separate factor",
                "budget to approve",
                "NOT_YET_DEFINED",
                "NOT_ESTABLISHED",
                "INDEPENDENT_REVIEW_PENDING",
                "NOT_EVALUATED",
                "NOT_EVALUATED",
                "docs/paper/HUMAN_REVIEW_PROTOCOL.md",
                "not retrospectively preregistered",
                "NOT_EXECUTED",
                "NOT_KNOWN",
            ]
        )
    write("EXPERIMENT_MATRIX.csv", fields, [dict(zip(fields, row)) for row in rows])
    paths = [x["source"] for x in claims] + [
        "crawler/web/fixture.py",
        "research/sprints/sprint-01/RESEARCH_QUESTIONS.md",
        "research/sprints/sprint-01/STUDY_PROTOCOL.md",
        "requirements-publication.lock",
        "docs/product/BASELINE_20261115.md",
        "docs/paper/RESEARCH_DOSSIER.md",
        "docs/paper/CLAIM_EVIDENCE_REGISTER.csv",
        "docs/paper/EXPERIMENT_MATRIX.csv",
        "docs/paper/LITERATURE.bib",
        "docs/paper/PAPER_OUTLINE.md",
        "docs/paper/HUMAN_REVIEW_PROTOCOL.md",
        "docs/paper/REPRODUCTION_GUIDE.md",
        "docs/paper/UX_EVALUATION_PROTOCOL.md",
        "docs/paper/OPEN_GATES.md",
    ]
    artifacts = []
    for path in sorted(set(paths)):
        artifacts.append(
            dict(
                path=path,
                sha256=sha(path),
                source_bytes=(ROOT / path).stat().st_size,
                access_rights="PUBLIC_OWN_OR_APPROVED_AGGREGATE",
                code_commit=code,
                corpus_binding="see study aggregate;synthetic fixture for chat",
                prompt_model="verbatim-qualification-v1;no LLM enabled",
                tokenizer="study-specific;NOT_KNOWN where absent",
                restore="checkout feature commit;public inputs only;see REPRODUCTION_GUIDE",
            )
        )
    write("ARTIFACT_MANIFEST.tsv", list(artifacts[0]), artifacts, "\t")
    print(f"{len(claims)} claims; {len(rows)} experiments; {len(artifacts)} hashed artifacts")


if __name__ == "__main__":
    main()
