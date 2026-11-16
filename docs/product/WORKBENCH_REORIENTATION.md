# Research workbench reorientation — 2026-11-16

Baseline e22750107a97ac81ab8572d61c6197d595a4d6b9, PR19. Existing RQ1–RQ8/H1–H6/T1–T9 and exposed historical results remain frozen. This is a product intervention, not a new independent study.

Priority: recover anonymous local sessions; place method choice at the question; controlled same-snapshot comparisons; account-free local annotations; improve answer-bound graph exploration. Existing blinded ReviewProtocol remains an optional separate adapter, not the main product. No synthetic performance or annotation may become Human Gold.

## P0 session contract

Expired or invalid bearer cookies rotate into a fresh random anonymous owner and CSRF token at the same-origin bootstrap endpoint. They never regain another owner's history. Existing 24h retention and purge remain unchanged. Writes require a live cookie and matching CSRF. Missing/expired sessions are 401; a wrong token on a valid session remains 403. Cross-origin bootstrap is forbidden. Client recovery is single-flight, refreshes CSRF, clears stale context on new ownership and re-executes a rejected question at most once in a new conversation. Other old-resource operations are not replayed under a new owner. No site-data deletion is required. Expiry does not promise retention of expired conversations.

Regression: backend fixation/IDOR/origin/expiry tests; actual Chrome expiry-answer-reload journey. Fresh synthetic database only. No corpus migration. Rollback: revert code; no schema change.

## Execution plan

1. P0 contracts and actual recovery browser proof.
2. Capability registry, visible selector, frozen-factor comparison records and exports.
3. Local single-reviewer append-only annotations, no login; preserve optional independent ReviewProtocol separately.
4. Corpus/adapter/rights inventory; activate only explicitly bound approved canonical inputs. Never replace E1 with relabeled code. No download/rebuild by default.
5. Graph/visualization integration and complete browser journey; canonical QA and publication manifests refreshed before release.


## Owner clarification of E1 code origin

On 2026-11-16 the owner answered “In eigener KI-Unterhaltung erzeugt” for the ZIP named by the original import record. This changes the previously unknown **code** origin status to owner-declared project AI-generated code. It is not a new claim about document rights or scientific quality. The original ZIP is absent at its recorded Downloads location; the import manifest remains available in the private worktree. Six original algorithm files are restored at their original paths, byte-for-byte, with hashes in E1_CODE_ORIGIN.json. Source inspection found stdlib/local imports, no external copyright notice or network/credential integration. E1/E2 differences are preserved. No code was renamed, algorithm rewritten, new ownership silently asserted, private fixtures copied or historical benchmark run modified. Root project MIT scope is applied to these owner-declared project sources; reassess if contrary third-party provenance appears.

## Real local composition

`--local-config generated/local-workbench.json` binds the existing workspace manifest, pre-existing complete FTS index, complete 384-dimensional dense index, resident quantized ONNX encoder and explicit original roots. Missing/mismatched files fail; no fallback DB, crawling, re-embedding, IE run or graph rebuild. Source/index hashes are recorded before/after local runs. Optional CPU dependencies are pinned in requirements-workbench-local.lock; no weights are downloaded or published.

The corpus is **REAL_LOCAL_RESEARCH**, not SYNTHETIC and not falsely labeled REAL LICENSED CORPUS. Private cached municipal documents are used only in the explicitly requested loopback research composition. Per-document redistribution licenses have not been established. No original passages or archives go into Git/PR/CI. Corpus source references and exact version hashes are visible locally; coverage/answer validity remain UNVALIDATED. The previously reported 2,056 frame / 4,564 version IDs / 176,176 units is an inventory union, not complete answerability.

Native BM25, Dense, Hybrid and Graph pipelines may access different structured relation information. Same question/context/snapshot/generator/budgets are frozen, but this native pipeline comparison **does not isolate graph format or IE effects**. The experiment receipt makes that confound explicit. SQL and qualified GraphRAG stay disabled until their actual adapters are configured/qualified. Recall/Precision/Claim Support/etc. remain NOT_EVALUATED without frozen independent reference labels.

Source provenance for the resident encoder: Xenova/paraphrase-multilingual-MiniLM-L12-v2 revision 2c4055b12046f11709e9df2c122e59ffbdc2f900, 384 dimensions, quantized ONNX 118,308,126 bytes. Existing manifests/hashes verified at runtime. The [upstream base model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) states Apache-2.0; the [pinned ONNX conversion card](https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/blob/2c4055b12046f11709e9df2c122e59ffbdc2f900/README.md) references that base model. Neither weights nor tokenizer are redistributed here. This is an embedding encoder, not an Answer LLM; the latter remains unqualified/inactive.


## Local event persistence and retention

Chat SQLite schema and 24h owner retention are unchanged; no corpus migration. Comparison receipts and local notes are separate private append-only event files, atomically published, <=1 MiB/event, <=4,096 versions per owner, <=64 MiB per event store, with unchanged reserve checks. They are retained as local research artifacts when a chat is deleted; API access still requires the creating anonymous session. Export before expiry; there is no hidden account/recovery of expired ownership. A local interaction is UNVERIFIED_LOCAL_INTERACTION, not verified human reading. Automated real-source browser drafts are explicitly AUTOMATED_TECHNICAL_TEST and never submitted as human labels.

## Current verification receipts

Public mechanical suite: **439 PASS / 6 SKIP, 9.07 s** in the existing environment (including optional encoder packages). Five explicit live-Neo4j skips and one undistributed D053 body. Focused P0 and workbench regressions cover fixation/expiry/CSRF/IDOR, frozen comparison factors and retained method errors, per-task labels, immutable local versions, frame resolver signature, topic-only false answers, no implicit missing-index build and synthetic-original refusal.

Actual Chrome: full anonymous synthetic journey PASS at 320/360/375/768/1024/1440, actual real-source journey PASS at 320/768/1440 (development-exposed Nennslingen case), no page errors, question/follow-up/comparison/graph/citation/chart-data/export/reload. No human acceptance, native zoom or screenreader conclusion. The real case had zero suitable chart rows: deterministic spec correctly refused a chart. Real archive/hash/sandbox readback is checked separately in the final run. Private original screenshots/exports remain local under generated/real-workbench-browser-*; they must not be uploaded. Synthetic screenshots are allowed CI artifacts.

Observed failures preserved: string-form browser wait under strict CSP (test harness changed to a function, CSP unchanged); overlapping comparison/chat scrollers (one result scroller and explicit comparison mode); unexpected comparison auto-restore on normal chat reload (restore on Compare only); missing required `resolve_ags` argument (new mechanical contract regression); thematic portal falsely admitted as measures evidence (conservative gate and synthetic negative regression). These are engineering observations, not independent expert labels.

Nine bound source/manifest/index hashes matched before and after real calls and browser use. One exposed Nennslingen native-pipeline observation returned lexical 5 / dense 2 / hybrid 4 / graph 1 qualified excerpt claims; observed wall times 0.502/0.888/0.204/0.055 seconds in that order, on the same snapshot. This is one warmed/sequential engineering observation, not an answer-quality metric, stable latency estimate or GraphRAG advantage. Earlier Regensburg portal admission is a negative diagnostic, not retained as a successful measures answer. All real extractive answers now remain PARTIAL/UNVALIDATED.

Final CI and clean-checkout results are reported on PR19 at the actual release head; source code and QA artifacts are hash-bound by the canonical publication registers.


Fresh checkout of source 9898fa2 with the public pinned environment: **439 PASS / 6 SKIP, 12.66 s**, one existing FastAPI/httpx deprecation warning. Ruff, compile and pip checks PASS. Original archival browser readback subsequently PASS (generated/real-workbench-browser-release-02), including exact original SHA header and sandbox CSP. Its initial test helper used a relative URL unsupported by APIRequestContext; changing only the helper to the explicit loopback base fixed the test. Source rendering/security gates were unchanged.


Fresh optional pinned CPU environment also composed the same real private snapshot and completed all four native methods (9898fa2), without model downloads, rebuilds or human labels. ONNX emitted an import-time platform telemetry-ID persistence warning. Follow-up f109f11 explicitly calls `onnxruntime.disable_telemetry_events()` before creating model sessions; actual composition PASS and 42 focused contracts PASS (3.47 s). Import initialization may still emit the ID-persistence warning before this API can be called. No corpus disclosure is inferred from that warning and no telemetry-free import is claimed.


### Public boundary re-audit after code-origin clarification

The old E1 collection exclusion is no longer valid. A 19-module re-audit first failed collection in eight modules because Protego was absent. Installing the existing project's exact Protego 0.7.0 and rerunning yielded **197 PASS / 12 FAIL (10.90 s)**. All twelve failures are concrete missing docs/pilot/minimal_external_preview/CANDIDATE_FACTS.json inputs, not relabeled successes. Those exact node IDs remain excluded with the actual missing-input reason. The other 197 technical cases were genuinely re-executed and reinstated without any assertion changes or overlap with the former selection. Expanded suite at 916eab0: **636 PASS / 6 SKIP (23.59 s)**. Previous 439 receipts remain historical narrower selections, not changed study results. Optional source/fixture artifacts beyond the six hash-bound algorithms are still not blanket-published.

Final app source 6d0f832 embeds the actual encoder manifest, runtime/parameters and index binding in each answer and controlled comparison receipt. Synthetic full journey PASS and real archived-original/full journey PASS again at that source. Nine private input hashes unchanged. Local notes use the archived original link where available; synthetic missing originals are never simulated. SQL/full GraphRAG inactive; no independent answer-quality metric.


### Clean-checkout correction of the expanded boundary

A truly public-only fresh environment at 916eab0 found **631 PASS / 5 FAIL / 6 SKIP (21.80 s)**. The five failures were the unchanged exact BM25-equivalence test's `git show b84db20:research/bavaria/workspace/core.py`: that historical object exists only in the owner's shared private repository. It was not fetched, reconstructed, replaced with current code or pushed. These five exact node IDs are now explicitly excluded from public replay with MISSING_NONPUBLIC_HISTORICAL_GIT_OBJECT_B84DB20. Their successful local run remains an owner-history-dependent engineering receipt, not public reproducibility. Thus **192** of the 197 locally re-enabled cases are self-contained for public inclusion. The 636-local receipt remains an intermediate result; current public denominator is corrected below. Original assertions and historical code unchanged.


### Current self-contained release result

At 5bd258e98d9899d812ccbe44138ab0f1b5ae9c63, owner-environment public selection **631 PASS / 6 SKIP (23.14 s)**; completely fresh public-only environment **631 PASS / 6 SKIP (20.47 s)** with existing FastAPI/httpx warning. The latter installed only requirements-publication.lock, no optional ONNX/tokenizers, Playwright, private input or private Git object. Static/compile/pip checks PASS. The 12 missing-CANDIDATE_FACTS and 5 private-history cases remain explicit exclusions, not skipped assertions or successes. Native Graph, IE and Answer quality remain unvalidated; no synthetic test count implies scientific accuracy.
