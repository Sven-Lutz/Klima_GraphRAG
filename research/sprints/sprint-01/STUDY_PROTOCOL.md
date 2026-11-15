# Reproducible experimental study protocol — pilot v1

Date: 2026-11-02. Status: frozen design intention for future method comparisons, conditional on M2/M3 qualification. Not a registration in an external registry and not a prospective registration of previously observed E2 outcomes. No M4 comparison is executed under this document.

## Scope, units and inherited exposure

Population of interest: German municipal climate-policy research; present convenience pilot: four E2 municipalities, subject to M2 audit. Corpus scope is the qualified frozen inventory, not all municipal policy on the web. Existing E0/DS/E2 and parser/heat-planning experiments have been inspected. E2 rankings and diagnostic outputs were already available before this protocol. E2-derived tasks are exploratory development material regardless of a later file label. Independent evaluation needs new blind references and unexposed source groups. A hash freeze proves immutability after a date, not historical independence.

Observation unit: task × method. Dependence units: municipality, document/version family, duplicated source body, shared evidence and paraphrase family. Multi-municipality comparisons link all constituent municipalities and documents. A connected component of these relationships stays in one split. If this leaves too few independent groups, report no held-out performance rather than split related evidence across train/test. Four municipalities and many questions are not many independent municipalities.

## Corpus and task construction

1. Run M2 on actual raw bytes and inherited manifests, recording missing files and mismatches. Freeze document IDs, body hashes, source URL, acquisition time, parser version and source-order text hashes. Keep source acquisition separate from parsing and chunking. Do not infer discarded documents from response/document count differences.
2. Record publisher, source authority evidence, administrative level, document jurisdiction, affected municipalities and claim-specific jurisdiction separately. Multi-jurisdiction documents are allowed. AGS is an identity field, not proof that all source statements concern that municipality.
3. Stratify candidate tasks by T1–T9 in RESEARCH_QUESTIONS, municipality, source type and temporal evidence. Derive feasible counts from authentic evidence. No preset 160 items. Record coverage gaps and exclusions. Historical sources are retained; acquisition date and statement/reference period are distinct.
4. Freeze candidate questions before inspecting future method outputs. Source-led annotation may see the complete qualified corpus and original pages/raw bodies, but not retrieved rankings. Existing E2 exposure is declared. Tasks without sufficient source verification remain candidates, never accepted gold.

## Annotation and acceptance

An annotation identifies question/task family, municipality set, required answer fields, source IDs/body hashes, exact quoted evidence, offsets or page/section locators, numeric value/unit/reference period, claim-specific jurisdiction, answerability and uncertainty. Distinguish original source facts from analyst interpretation. A relation needs both endpoints and an evidenced predicate; co-occurrence is insufficient. Cross-source temporal change needs compatible concepts/units and at least two dated states. Plan/adoption/implementation/effect are distinct labels.

Answerability labels are bounded to the frozen corpus and question date. A negative requires a documented search/review scope over that corpus; top-k retrieval failure is insufficient. False premises require an explicit correction supported by evidence. Genuine absence and unavailable/undecodable evidence are separate cases. Wrong municipality is checked against substantive claim scope, not name frequency. Ambiguous questions may require clarification rather than refusal.

The first assessor records evidence and rationale; a second assessor independently checks the relevant originals and labels without method outputs. Record actual assessor identities/roles, timestamps, exposure and disagreements. Adjudication records the resolution and rationale; unresolved items remain quarantined. No two model passes are claimed as two human annotators. If only source-verified agent candidates exist, release them as provisional and block independent-gold acceptance. An independent researcher may later verify the packet; no fabricated person or annotation event is permitted. Agreement is reported only if real duplicate annotation exists, with denominator and metric appropriate to labels.

## Split and leakage policy

Maintain explicit `development`, `test`, and `quarantine` membership plus exposure flags. Allocate evidence-connected components, not individual question rows. Duplicate bodies, document revisions and answer paraphrases cannot bridge dev/test. Repeated page boilerplate is audited for shared informative content. Sources needed by a cross-municipality item belong to its joint component. Models never receive reference answers/rationales as indexable sources. Reference files and method outputs are distinct. Persist the hash of the split assignment before comparative runs. Hyperparameter selection uses development only; contaminated or too-small test components are reported as limitations, not silently relabeled.

## Common method contract and factors

Each adapter receives the same frozen question, permitted source IDs and budget config; returns ranked source/evidence IDs, extracted fields, explicit abstention/partial-answer status, and resource/provenance logs. A generation arm additionally returns atomic claims and supporting citations. A fixture adapter verifies mechanics only. Unsupported metrics are null/unsupported, not zero quality.

Separate factors: acquisition coverage; parser conversion; chunk boundaries; retrieval; structured extraction; representation; iteration/tool use; generation and verification. Do not vary all simultaneously and attribute effects to graphs. Hold corpus, task set and generator constant for retrieval ablations. Record model/provider version, prompts, tokenizer, index config, reranking/fusion, seeds, k and source scope. Log preprocessing/build/update work as well as query work. Budget caps must be common and specified in the future run config before outputs: context tokens, candidate pools, tool calls, wall time, monetary/API limits. Unapproved paid calls are not required to complete Sprint 1.

## Outcomes and denominator rules

Retrieval: per-task evidence Recall@k = relevant evidence IDs retrieved / all verified relevant IDs; Precision@k = relevant IDs retrieved / returned IDs in first k. Deduplicate repeated IDs. Report k, actual returned count, evidence granularity and denominator. Empty return with positive gold gives recall zero; precision convention is explicit. Empty or incomplete positive reference sets cannot support recall claims. Negative tasks use abstention/false-positive outcomes, not recall with denominator zero. MRR is optional only for tasks with a well-defined relevant hit; multi-source bundle completeness is reported separately.

Extraction: field-level TP/FP/FN, typed numeric value/unit and reference period, explicit unknown/absent fields, and raw counts. A correct value with wrong municipality or time is wrong. Normalization rules are frozen; tolerances require domain justification and cannot be invented after seeing errors. Partial-answer accuracy and completeness are separate.

Generation (Sprint 2 only): atomic-claim support, citation correctness, factual completeness, jurisdiction/date validity, conflict disclosure and appropriate abstention checked against sources. Judge outputs are judgments with calibration limitations. No exact-match or LLM score is relabeled factual truth. No single average combines retrieval, extraction, generation and resources.

## Analysis and stopping rules

Report per-item outcomes, per-task-family and municipality macro summaries, plus paired method differences for hypotheses H1–H6. Publish raw counts and all exclusions. With four municipalities use descriptive ranges, leave-one-municipality-out sensitivity where valid, and explicit dependence caveats. Cluster/nested resampling may be exploratory after cluster definitions are frozen; four clusters do not yield reliable national uncertainty. No power claim, multiple-testing significance search, universal method ranking or practical threshold is authorized by this pilot. Additional municipalities would be required for confirmatory generalization.

Stop scientific benchmark claims if M2 integrity fails, labels lack independent verification, splits leak, or denominators are unsupported. Technical harness smoke tests can still be run on known-reference fixtures and must be labeled mechanical verification. Preserve failed runs and their errors. Amendments receive date, reason, affected files and whether outputs had been seen; do not overwrite historical E2 protocols. Sprint 1 closes after M3 readiness assessment. M4 controlled comparisons and M5 architecture decision require Sprint 2.

## Reproduction package

Archive code commit, corpus/benchmark/config hashes, dependency/runtime versions, run command, seed, input/output identifiers, start/end timestamps, processing status and actual resource measurements. Version the small manifests, schemas, protocols and fixtures; keep raw sources external/ignored until redistribution rights are established. M2 and M3 reports supply actual commands, hashes, results and acceptance status; this protocol alone establishes none of them.

## Amendment P1 — scope-limited pilot approval, 2026-11-02

The project owner explicitly approved the ten existing reference-answer texts as
PILOT_GOLD_APPROVED in Issue #7 comment6584006353 and the current execution task.
This is a retrospective, bounded development freeze, not prospective registration
or external research-gold validation. For this pilot, a second independent human
annotation is not a release prerequisite. The earlier independent-annotation,
original-document and coverage conditions continue to govern the larger
RESEARCH_GOLD_PENDING_EXTERNAL_VALIDATION release and scientific generalization.

No historical candidate, PARTIAL label, human testimony or review-access mode is
changed. Versioned orthogonal metadata separates the concrete source question,
claim support and unknown real-world state; proposed reinterpretations are not
represented as newly adjudicated labels. Existing qrel/field/claim denominators
are not manufactured by calling the answer texts gold. All ten cases remain
DEVELOPMENT_EXPOSED, not an independent held-out test set. No M4/M5 run is
authorized or performed by this amendment. See M3_PILOT_GOLD_RELEASE.md for the
manifest, exact owner decision, technical regression baseline and allowed uses.


Publication note (2026-11-15): original design text preserved. References to local Foundation, M2/M3, pilot-Gold decisions and historical data are historical dependencies not supplied or independently validated by this code publication. No review identities or reference answers are distributed.
