"""Run the EXISTING canonical municipal policy answer generator on verified V4 evidence.

First: python klima_answer_v4.py --evidence evaluation/regensburg_pv_evidence_v4.json --validate-only
Then:  python klima_answer_v4.py --evidence evaluation/regensburg_pv_evidence_v4.json --generate

The evidence adapter rechecks every selected chunk against the original SQLite DB.
This is a saved-V4-evidence evaluation path, not automatic live retrieval.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from crawler.core.answer_context import EvidenceBudget, POLICY_CLASSIFICATION_PROMPT_VERSION_V9, build_answer_context
from crawler.core.answer_generation import (
    AnswerRequest, CanonicalAnswerGenerator, GenerationParameters,
    JsonlAnswerRunRecorder, OllamaGenerationProvider,
    render_answer_markdown,
)
from crawler.core.question_intent import extract_question_intent
from crawler.core.retrieval import RetrievalMode, RetrievalRequest
from klima_v4_evidence_adapter import V4EvidenceRetriever


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', required=True, type=Path, help='A JSON saved by klima_evidence_v4.py')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--validate-only', action='store_true', help='Verify JSON against SQLite without Ollama')
    group.add_argument('--generate', action='store_true', help='Call canonical answer generator with Ollama')
    p.add_argument('--model', default='llama3.1:8b')
    p.add_argument('--endpoint', default='http://127.0.0.1:11434')
    p.add_argument('--top-k', type=int, default=16)
    p.add_argument('--timeout', type=float, default=240.0)
    p.add_argument('--output', type=Path, default=None)
    args = p.parse_args()

    root = Path(__file__).resolve().parent
    evidence_path = (root / args.evidence).resolve()
    payload = json.loads(evidence_path.read_text(encoding='utf-8'))
    intent = extract_question_intent(payload.get('query', ''))
    if intent.municipality_id != payload.get('municipality_id'):
        raise SystemExit('Question municipality does not match the V4 evidence municipality')
    if not intent.policy_family or not intent.municipality_name:
        raise SystemExit('Policy family not identified; abstaining instead of guessing')
    if args.top_k < 1 or args.top_k > 100:
        raise SystemExit('--top-k must be between 1 and 100')
    evidence_adapter = V4EvidenceRetriever(evidence_path, root, municipality_name=intent.municipality_name)
    rr = RetrievalRequest(
        query=payload['query'], manifest_path=str(evidence_path),
        mode=RetrievalMode.LEXICAL, top_k=args.top_k,
        municipality_ids=(payload['municipality_id'],),
    )
    request = AnswerRequest(
        question=payload['query'], retrieval_request=rr,
        database_snapshot_id=evidence_adapter.snapshot_id,
        generation_provider='ollama', generation_model=args.model,
        generation_parameters=GenerationParameters(
            temperature=0.0, max_output_tokens=1024,
            timeout_seconds=args.timeout,
        ),
        prompt_version=POLICY_CLASSIFICATION_PROMPT_VERSION_V9,
        evidence_budget=EvidenceBudget(max_items=args.top_k, max_characters=48000),
        policy_quote_audit=True,
    )
    request.validate()
    retrieval = evidence_adapter.retrieve(rr)
    CanonicalAnswerGenerator._validate_retrieval_binding(request, retrieval)
    context = build_answer_context(
        retrieval, budget=request.evidence_budget, prompt_version=request.prompt_version,
    )
    print('QUESTION:', request.question)
    print('POLICY FAMILY:', intent.policy_family)
    print('MUNICIPALITY:', intent.municipality_name, intent.municipality_id)
    print('VERIFIED ORIGINAL CHUNKS:', len(retrieval.items))
    print('CITABLE EVIDENCE:', len(context.citations.citations))
    for citation, eid in zip(context.citations.citations,
                             context.selected_evidence_ids, strict=True):
        item = next(e for e in retrieval.items if e.evidence_id == eid)
        kind = item.retrieval_traces[0].method
        print(f'  [{citation.label}] {kind}: {item.document.source_url}')
    if args.validate_only:
        print('VALIDATION PASSED: Source text and provenance match original SQLite.')
        print('No Ollama call, no answer classification, no DB changes.')
        return
    (root / 'evaluation').mkdir(exist_ok=True)
    recorder = JsonlAnswerRunRecorder(root / 'evaluation' / 'policy_v4_answer_runs.jsonl')
    provider = OllamaGenerationProvider(args.endpoint)
    generator = CanonicalAnswerGenerator(
        retriever=evidence_adapter, providers={'ollama':provider}, run_recorder=recorder,
    )
    previous = os.environ.get('KLIMA_POLICY_CLAIMS_V1')
    os.environ['KLIMA_POLICY_CLAIMS_V1'] = '1'
    try:
        result = generator.generate(request)
    finally:
        if previous is None:
            os.environ.pop('KLIMA_POLICY_CLAIMS_V1', None)
        else:
            os.environ['KLIMA_POLICY_CLAIMS_V1'] = previous
    print('\n' + render_answer_markdown(result))
    if args.output:
        target = (root / args.output).resolve()
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
        print('SAVED RESULT:', target)
    print('LIMITATION: Saved evidence cannot prove new budget availability or live application status.')


if __name__ == '__main__':
    main()
