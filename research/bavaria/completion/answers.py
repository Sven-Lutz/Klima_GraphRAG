# ruff: noqa: E501
"""Equal-evidence deterministic versus local-model answer qualification."""

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from crawler.core.answer_context import EvidenceBudget
from crawler.core.answer_generation import GenerationParameters
from research.bavaria.workspace.core import Workspace, answer

CASES = [
    (
        "A01",
        "Wer ist Auftraggeber und wer Auftragnehmer des Wärmeplanungskonvois Aiterhofen-Salching?",
        ["09278167"],
        "VG Aiterhofen commissions; CreaTech authors; no automatic Salching-only attribution.",
    ),
    (
        "A02",
        "Welche beiden Büros wurden für Moosburgs Energiewende beauftragt und welches Erneuerbaren-Ziel soll erreicht werden?",
        ["09178143"],
        "KlimaKom eG and Green City Energy AG, 100 percent renewables by 2035 is a target, not measured achievement.",
    ),
    (
        "A03",
        "Welchen Status berichtet Hof für die getrennt codierten Maßnahmen M.5.5 und M.7.13 im Januar 2024?",
        ["09464000"],
        "Visual original: M.5.5 in process, M.7.13 not begun; no current or physical-project-identity assertion.",
    ),
    (
        "A04",
        "Wer ist Fördergeber der Wärmeplanung Eltmann und bis wann wurde die Förderphase verlängert?",
        ["09674133"],
        "BMWK; funding end extended to 31.03.2026; not proof of operating heat infrastructure.",
    ),
    (
        "A05",
        "Ist die Wärmeversorgung in Fensterbach aktuell vollständig umgesetzt?",
        ["09376125"],
        "Abstain about current implementation; original October 2025 only future measures.",
    ),
    (
        "A06",
        "Wie viel Klimaschutzförderung wurde Jettingen-Scheppach im Bauplatz-Antragsbogen tatsächlich ausgezahlt?",
        ["09774144"],
        "No qualified climate-grant payment evidence; abstain without real-world absence claim.",
    ),
]


def run(db, relations, manifest, output, model="qwen3:1.7b"):
    root = Path(output)
    if root.exists():
        raise ValueError("Use a fresh answer study directory")
    if shutil.disk_usage(root.parent).free < 15 * 1024**3 + 128 * 1024**2:
        raise RuntimeError("Reserve")
    w = Workspace(db, relations, manifest)
    # Resolve exact official names from bound registry rather than trust hand-typed IDs.
    selected_names = [
        "Salching",
        "Moosburg an der Isar",
        "Hof",
        "Eltmann",
        "Fensterbach",
        "Jettingen-Scheppach",
    ]
    corrected = []
    for case, name in zip(CASES, selected_names):
        matches = [ags for ags, r in w.municipalities.items() if r["name"] == name]
        if len(matches) != 1:
            raise ValueError(name)
        corrected.append((case[0], case[1], matches, case[3]))
    root.mkdir(parents=True)
    freeze = {
        "status": "FROZEN_BEFORE_GENERATION",
        "cases": corrected,
        "seed": 42,
        "model": model,
        "context_characters": 6000,
        "max_items": 8,
        "model_context_tokens": 4096,
        "max_output_tokens": 512,
        "max_local_provider_calls": 6,
        "wall_budget_seconds": 600,
        "quality_gate": {
            "critical_role_status_scope_errors_max": 0,
            "unsupported_claims_max": 0,
            "answerable_completeness_min": 0.80,
            "unknown_abstention_required": True,
        },
        "labels": "PROVISIONAL_SOURCE_FIRST_AGENT_NOT_HUMAN_GOLD",
        "logical_oracle": False,
        "scope": "Explicit municipal selector; same heuristic retrieval and evidence budget per provider. This isolates generation conditional on those selected contexts.",
        "code_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                Path("research/bavaria/workspace/core.py"),
                Path("crawler/core/answer_generation.py"),
            ]
        },
        "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "FREEZE.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n")
    start = time.monotonic()
    rows = []
    budget = EvidenceBudget(max_items=8, max_characters=6000)
    params = GenerationParameters(
        temperature=0,
        seed=42,
        max_output_tokens=512,
        timeout_seconds=90,
        context_window_tokens=4096,
        num_threads=1,
        think=False,
    )
    for identifier, question, ags, _ in corrected:
        equality = []
        for provider in ["workspace_rules", "ollama"]:
            if time.monotonic() - start > 600:
                raise RuntimeError("Answer wall budget; retain partial results")
            result = answer(
                w,
                question,
                root / "runs.jsonl",
                ags,
                provider=provider,
                model=model if provider == "ollama" else "workspace-relation-composer-v1",
                ollama_url="http://127.0.0.1:11435",
                evidence_budget=budget,
                generation_parameters=params,
            )
            (root / (identifier + "-" + provider + ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            canonical = result["canonical_answer"]
            selected = canonical["evidence_selection"]["selected_evidence_ids"]
            equality.append(selected)
            rows.append(
                {
                    "case_id": identifier,
                    "provider": provider,
                    "status": canonical["status"],
                    "answer": canonical["answer_text"],
                    "selected_evidence": selected,
                    "generation": canonical["generation"],
                }
            )
            print(identifier, provider, canonical["status"], flush=True)
        if equality[0] != equality[1]:
            raise RuntimeError("Evidence access differs across generators")
    (root / "report.json").write_text(
        json.dumps(
            {
                "results": rows,
                "seconds": time.monotonic() - start,
                "same_selected_evidence_verified": True,
                "independent_human_gold": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    run(
        "generated/bavaria/acquisition.sqlite",
        "generated/bavaria/completion/relations01/evidence.sqlite",
        "generated/bavaria/completion/workspace-manifest.json",
        "generated/bavaria/completion/answers01",
    )
