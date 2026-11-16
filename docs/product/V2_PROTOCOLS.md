# V2 contracts and annotation guide

## Conversation and retrieval

`conversation-state-v2.1` records the current/effective question, explicit scope, parent IDs, referenced evidence IDs, open aspects and structured decisions. Explicit place changes clear inherited time/evidence. Unknown named places require authoritative ID resolution; no model guesses IDs. Topic inheritance is bounded to identified deictic patterns. Emissions boundary and success definition can require clarification. Unresolved entities/measures remain empty rather than invented. Events are embedded in immutable turn versions; no historical turns are rewritten.

`intent-router-v2.1` separates intent, selected available method, natural-language formalization, representation and generator. Relation questions may select graph; other tasks use an available text adapter. This is a heuristic, not validated planner accuracy. SQL/graph equivalence and relations-information ablation remain controlled factors of the existing study. No new SQL backend or global community GraphRAG is claimed. See existing query_planning.py and canonical adapters for additional research routing; V2 does not silently equate their information conditions.

## Generation and model registry

Default: `deterministic_extractive`. Optional: `conversational-claims-v2.1`, JSON claims with exact evidence IDs/spans. Numeric-token and modal checks are mechanical. Municipality attribution, causal implications, full aspect coverage and semantic entailment still require independent review. Invalid/provider-failed output yields ERROR with no silent extractive fallback. Empty output claims yields explicit abstention. Generated responses are conservatively PARTIAL. No streaming is claimed: current provider requests use stream=false.

Four independent settings roles: answer, IE, retrieval/ranking, verification/evaluation. IE model choice never changes existing index content. A new extraction experiment requires new output directory, source hashes, schema/config/model/prompt binding, qualification and explicit promotion; historical runs remain immutable. Eight exposed cases / 56 dependent slots remain insufficient for broad LLM-IE release.

CLI `--answer-config PATH` accepts only explicit local Ollama configuration with model digest, bounded weights, reviewed license and hash-bound qualification artifact. No model is currently enabled/qualified by V2. Example schema (placeholders, **not approval**):

```json
{"provider":"ollama","model":"OWNER_QUALIFIED_TAG","digest":"sha256:OWNER_DIGEST",
 "execution":"local","qualification":"EXPLICITLY_QUALIFIED",
 "qualification_artifact":"owner-qualification.json","qualification_sha256":"OWNER_HASH",
 "license_reviewed":true,"weights_bytes":522640096}
```

The qualification artifact must document source/model license, input and output hashes, prompt, budgets, technical outcomes, semantic review status and limitations. A hash proves artifact identity, not truth or independence. The operator must supply actual qualification; do not create one merely to enable the toggle. Before each inference the local `/api/tags` inventory must contain the exact configured tag/digest. Only 127.0.0.1:11434, one worker, 4096 context tokens, 512 output tokens, two threads, 15-second provider timeout. A conservative UTF-8 input-byte bound plus 1024 template-overhead and 512 output tokens rejects oversized requests before provider invocation; no implicit evidence truncation. This bound is not measured model tokenization. No API costs/downloads authorized. Physical RAM qualification remains necessary; weights size alone is not a runtime-memory guarantee.

## Visualization

`visualization-v2.1`: server extracts typed numeric observations from evidence quotes, not generated answer prose. Original value, unit, modality, municipality IDs, evidence ID, passage hash, snapshot and turn bind each row. Ambiguous thousands/decimal notation is skipped. No sums, inferred dates, conversions, cross-municipality comparison or temporal series. Multiple/unqualified metrics render a table; only one compatible observation can render the bounded bar. Unknown time and source redistribution rights remain explicit. Original rights are not cleared by producing an export; no external publication of local datasets is authorized. Accessible data table and JSON export always accompany rendered data.

## Review and annotation guide

Source screening: relevance, authority, source version and municipality attribution. It does not validate every claim in that document.

Claim validation: atomic statement including amount/unit, place, time, modality and actor. Supported = all required aspects carried; Partially supported = named subset carried; Unsupported = passage does not carry the assertion; Contradicted = passage supports an incompatible assertion; Insufficient evidence = judgment cannot be made from available original material; Ambiguous/Needs review = interpretation unresolved, explain why. Absence in a bounded passage is not evidence of real-world absence. Null dimensions remain unjudged.

IE/relation validation: individual field values and endpoints, roles, relation type, scope and temporal validity. Co-occurrence is not a relation; graph paths are not causality.

Answer validation: question relevance, aspect completeness, scope, time and appropriate abstention. Citation validity alone does not establish answer truth. Existing generic decisions are not one shared accuracy label across these units. Claim dimension fields use the canonical metric_judgments contract; source/IE/answer judgments must not be inferred from them.

Queue priority is the existing operator heuristic, not scientific information gain or a probability sample. Deduplication/version identity and two blinded first judgments remain owned by ReviewProtocol. Autosave records private drafts; final judgments remain immutable. Alt+1–6 selects a label, Ctrl/Cmd+S saves. UI interaction time is self-reported telemetry, not proof of reading, attention or reviewer efficiency. A click on Original does not prove understanding. Demo A/B/adjudicator are synthetic accounts and do not represent recruited independent humans. Chatfeedback cannot become Gold.

## Evaluation and leakage

New V2 technical fixtures remain SYNTHETIC_MECHANICAL and development-exposed. V1 versus V2 UX, extractive versus qualified local synthesis and claim-gate ablations require identical evidence, corpus, budgets and independent reference conditions. No new empirical comparison has been run. RQ1–RQ8/H1–H6/T1–T9, historical aggregate results and negative IE decisions remain unchanged. Cluster by municipality/document family/connected evidence component; do not treat dependent slots or questions as independent observations. Dataset/split/method/budget freeze, human review and exposure analysis precede confirmatory evaluation. Existing exposed cases cannot become a blind holdout through a new hash.


### Unit-specific masks and bounded queues

`unit-review-v2.1` adds source relevance/authority/scope/version; IE value/unit/role/scope/time; relation endpoints/type/roles/scope/time; and answer relevance/completeness/scope/time/abstention as separate enum/null fields. Claim metrics retain the original Boolean/null contract. Per-unit judgments live in the existing `corrections.unit_judgments` with a server-bound schema version; existing Protocol comparison already treats differing corrections as a conflict. Unsupported fields/values/versions fail. No old SUPPORTED value is expanded into dimension labels. Study-required fields and domain acceptance still require a frozen annotation guide; these are technical masks, not a new independent Gold standard.

Queues expose limit/offset (50 by default; <=200), own-role progress, kind filters and next-page navigation. Reviewer projection reads assignments, own submitted IDs and objects once; it does not expose partner votes or consolidated conflict state. Admin/adjudicator authorization/views remain unchanged. Tab-local filters clear on logout/account switch; drafts stay server-side and immutable final judgments remain locked. SUPPORTED needs no repeated comment; partial/negative/ambiguous decisions retain the canonical rationale requirement.

The source-only load command is `.venv/bin/python -m scripts.qa.review_queue_load --directory generated/queue-load-new`; a new isolated path is required. `--measure-existing --compare-v1` is only for that explicitly bound synthetic fixture. It extracts the reviewed V1 queue method from the fixed public commit, compares the exact returned queue and hashes the DB before/after. No judgments are generated. An initial CLI invocation without `-m` failed import; the documented module invocation is required. An initial hash assertion correctly detected an export receipt written by the test helper; the helper now verifies zero review events without materializing Gold. See V2_QUEUE_LOAD.json for the single paired observation; no human-review efficiency or general UX superiority follows.


## Workbench intervention (2026-11-16)

The primary anonymous workflow is research chat → visible capability-bound method choice → same-snapshot native-pipeline comparison → sources/archived originals → answer-bound graph/data → optional local note/export. The separate existing ReviewProtocol remains unchanged as an independent-annotation adapter, not mandatory application identity. Account-free local-note schemas are separate per object kind; drafts are not submitted judgments and neither is independent Gold. Event retention and bounded storage are documented in WORKBENCH_REORIENTATION.md.

Real local composition reuses original BM25/FTS, dense index, Hybrid and graph adapters. Dense encoder is a resident 384-D quantized ONNX embedding model, unrelated to Answer LLM/IE selection. Configuration hashes, actual model manifest, snapshot and technical qualification are exported. Original bodies are bounded/hash-verified and served with a source-specific sandbox CSP. Extraction, corpus, index and historical graph are not modified. Native method comparison freezes other settings but explicitly does not isolate structured-information or graph-format effects. Quality metrics without independent references remain null/NOT_EVALUATED.
