"""Reproduce one explicitly configured local synthesis from a saved retrieval receipt."""

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from crawler.core.answer_context import EvidenceBudget, build_answer_context
from crawler.core.answer_generation import GenerationInput, OllamaGenerationProvider
from crawler.web.grounded_provider import EvidenceBoundGenerationProvider, GroundedModelConfig
from crawler.web.research_restore import restore_retrieval


def run(config, retrieval, output):
    config = GroundedModelConfig.read(config)
    output.mkdir(parents=True, exist_ok=False)
    saved = json.loads(retrieval.read_text())
    raw = saved.get("result", saved.get("retrieval_result", saved))
    result = restore_retrieval(raw, raw["database_snapshot_id"])
    context = build_answer_context(
        result, budget=EvidenceBudget(config.max_evidence_items, config.max_evidence_characters)
    )

    class Capture:
        def generate(self, value):
            response = OllamaGenerationProvider(config.endpoint).generate(value)
            (output / "raw-response.json").write_text(
                json.dumps(asdict(response), ensure_ascii=False, indent=2)
            )
            return response

    provider = EvidenceBoundGenerationProvider(
        config, transport=Capture(), audit_path=output / "runs.jsonl"
    )
    # Capture is diagnostic only; keep the production model-digest check enabled.
    provider.probe_model = True
    started = time.monotonic()
    try:
        response = provider.generate(
            GenerationInput(
                context.system_instructions,
                result.query,
                context.evidence_context,
                context.allowed_citation_labels,
                context.prompt_version,
                config.model,
                config.parameters,
            )
        )
        record = dict(
            status="EXECUTED_CONTENT_REVIEW_REQUIRED",
            response=asdict(response),
            validation=provider.validation_for(response.text),
        )
    except Exception as exc:
        record = dict(status="FAILED", error_class=type(exc).__name__, error=str(exc))
    record.update(
        elapsed_seconds=time.monotonic() - started,
        scientific_gold=False,
        snapshot_id=result.database_snapshot_id,
    )
    (output / "RESULT.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(record["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["config", "retrieval", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    run(**vars(parser.parse_args()))
