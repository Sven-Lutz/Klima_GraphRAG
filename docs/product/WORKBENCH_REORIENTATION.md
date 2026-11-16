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
