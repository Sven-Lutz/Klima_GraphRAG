# V2 technical candidate and owner acceptance

16.11.2026. Base `0483eef9d8bb9c37559d8dcee0e0b06449d9a394`; architecture delivery commit `729f55c`. **Programme incomplete; no overall release PASS.** Current code is a bounded synthetic product candidate, not a qualified municipal research assistant.

## Implemented and checked scope

Versioned dialogue decisions, topic inheritance, explicit place/time isolation and conservative clarifications; capability-bounded intent trace; optional hash-qualified local generation adapter with no silent fallback; safe Markdown with HTML/images disabled; bounded graph workspace with filter/layout/table/evidence/export; server-derived numeric observations with compatible single-observation rendering/table/export; four separate settings roles; explicit coverage unknowns; direct synthetic review queues, private autosave, own progress, claim dimensions and account-switch isolation. Existing extractive baseline, canonical retrieval/citations and append-only blinded ReviewProtocol are retained.

No private originals, vendor E1 files, model weights or real identities/labels added. No migration, crawl, graph rebuild, deployment, merge or external inference. Own numerical inventory only; original rights remain separate. Markdown-it 14.1.0 came from the npm package, with original MIT license: bundle SHA256 `38c70a1e7ca91ab40e2d9e6e60129851a717ed1c7d4acbbdd41bf9503791cf68`, license SHA256 `792c48c5a849a15fdf9e37e8bcf9e6d1dd13b32b46c642a748a0a46a9919d473`. E1 exclusion remains untouched.

## Actual tests and negative results

- V1 conversation suite: 17 PASS after fixing three initial regressions; assertions unchanged. Unsupported fixture prefilters were reverted to explicitly recorded postfilter semantics; unresolved named places cannot return foreign fixture passages.
- V2 plus V1: latest store-capable focused run 34 PASS, 2.62 s. Then additional pure context/budget/duplicate-observation regressions were added. Latest pure V2 subset: 22 PASS / 3 deliberately deselected store tests, 0.28 s. These fixtures do not measure municipal accuracy.
- Public suite before resource loss: 414 PASS / 6 SKIP, 8.73 s (`generated/v2-public-tests-final.txt`). Earlier V2 run 411 PASS / 6 SKIP, 7.45 s. These are successive snapshots, not additive test counts.
- **Latest full attempt: 400 PASS / 7 FAIL / 8 ERROR / 6 SKIP, 6.26 s**, all 15 observed FAIL/ERROR at unchanged 15-GiB/chat-reserve gates (`generated/v2-public-tests-release.txt`). This is not a current full-suite PASS. Five skips: disposable Neo4j not enabled; one: optional archived D053 original not distributed.
- Ruff, Python compile, JS syntax, pip check and historical aggregate arithmetic checks PASS. Dossier checker validates frozen V1 hashes from Git; V2 current hashes separately refreshed. Fresh isolated dependency installation was not repeated locally: current free-space headroom is insufficient for another environment without crossing the existing reserve.
- Unchanged V1 Chrome workflow PASS with Playwright 1.65.2: citation/Escape, follow-up, abstention, Research/Validation, four widths, no page errors. One observed answer-to-DOM duration ~0.108 s is a synthetic single-run timing, not representative latency or quality.
- New V2 Chrome receipt `generated/v2-workspaces-final/receipts.json`: Markdown headings/tables and injection rejection, graph filter/table/evidence, 36 route/viewport combinations (320, 360, 375, 768, 1024, 1440 over six routes), 320×568 composer, dataset export, autosave and blinded account switch PASS. No page errors. Later minor table/rail/observation grouping changes have pure/static checks; latest browser rerun failed to connect after the resource-gated server restart. **No final-browser PASS for those later changes.**
- Actual screenshot inspection: mobile composer, desktop graph, mobile tables, Validation. Found and fixed letter-by-letter mobile table wrapping, shrinking new-chat action and account-switch stale draft. Viewport checks are not WCAG certification, screenreader testing or native zoom. Native CUA access attempted: browser host unavailable; native zoom NOT_TESTED.
- Initial Playwright 1.56.0 hit a Chrome CSP evaluation incompatibility; aligned to project-CI 1.65.2, security policy unchanged. Browser scripts now use context managers so assertion failures also close browsers normally. Latest cleanup change is syntax/static checked; resource-gated rerun pending.
- Model probe: manifests found, blobs are nonresident placeholders (0 allocated blocks). Read/hash interrupted, loopback tag request timed out, server stopped. No successful model-blob digest verification, no LLM inference, no semantic qualification, no paid API call. See coverage/model inventory.
- Root directory secrets scan reported five findings exclusively in ignored generated credentials/baseline/tool files. No such files are publication candidates. An explicit 38-file source-only staging scan subsequently found 0 secrets (403 KB scanned); new Git history is checked separately. The broad directory scan was not clean.

## Regression register

| Case | Cause / fix | Evidence |
|---|---|---|
| V2 fixture prefilter crash | Canonical fixture has no filter adapter; record fixture postfilter limitation, forward scope only to real adapters | unchanged V1 tests 17 PASS |
| New place with no established scope | Unconditional clarification changed V1 abstention; preserve abstention and block unknown-place retrieval | V1/API/browser regression PASS |
| Reviewer queue undefined state | UI read review_state while queue returns state | explicit queue contract and browser flow |
| Reviewer-switch draft flash/race | Old DOM and asynchronous response survived account switch; clear immediately, cancel timer, version responses | failing first browser run; corrected repeat PASS |
| Unit/sign/Unicode numeric mutation | Numeric presence alone insufficient; normalize/check signed tokens and units | synthetic negative tests |
| Duplicate numeric observations | Multiple evidence representations must not be summed | new pure grouping regression PASS |
| Browser failure lifecycle | Exception skipped explicit close | context-manager fix; runtime rerun pending resource recovery |
| Resource gate | Free bytes fell below reserve during programme; preserve gate, report failed run | latest full attempt and blocked restart |

## Separate acceptance states

| Area | Current state |
|---|---|
| ENGINEERING | PARTIAL; latest full local run/resource-gated; current static/pure checks pass |
| ANSWER QUALITY | NOT_INDEPENDENTLY_VALIDATED; no real LLM run |
| UX | TECHNICAL_PARTIAL; actual screenshots inspected; final minor changes and human/assistive acceptance pending |
| CORPUS INTEGRATION | BLOCKED_FOR_PUBLIC_FULL_CORPUS by E1/original rights; private aggregate inventory only |
| REVIEW WORKFLOW | SYNTHETIC_TECHNICAL_PARTIAL; blinded Protocol retained; no real workload acceptance |
| HUMAN GOLD | NOT_ESTABLISHED; zero recruited independent reviewers |
| REPRODUCIBILITY | PARTIAL; pinned environment and synthetic checks; latest local resource failure explicit |
| EXTERNAL PREVIEW | NOT_DEPLOYED; no external acceptance |
| PAPER READINESS | NOT_READY; original scientific/Human/rights gates remain |

## Run and owner demonstration

```bash
.venv/bin/python -m crawler.web --fixture --directory generated/chat-v2 --port 8825
```

URL `http://127.0.0.1:8825`. Current start is blocked until the selected physical volume has the unchanged reserve: at least 15 GiB + 64 MiB for chat, with >=16 GiB recommended for testing/export headroom. No server is currently promised running. No gate reduction or alternate implicit database. A separately specified output directory can live on an adequately free owner-approved volume.

No API keys/model configuration needed for the **synthetic** default. Existing local .venv has pinned project dependencies; setup for a clean checkout remains REPRODUCING.md. Optional `--answer-config` requires actual model/license/qualification evidence; none is supplied or fabricated.

1. Chat: “Welche Solarprojekte planen die Stadtwerke?” → synthetic PLAN with 20 kWp; open its citation. Follow with “Wie viel Leistung?” then “Welche wurden bereits umgesetzt?”; planning must not become measured implementation.
2. Graph: “Wer plant die Solaranlage?” → Graph öffnen → filter relation/node, change layout, inspect edge/table evidence, export. Bound to this answer; no global completeness/causality.
3. Chart: open Diagramm / Daten on the solar answer. Data come from the passage, with PLAN, unknown event time and snapshot. Single observation/table only, no invented trend or sums. Inspect/export JSON.
4. Validation: `/validation` → Demo-Prüfkonto A → choose one of four separate synthetic object kinds → Original → draft. Switch to B: A's draft must be absent. No labels are prefilled or automatically submitted; all demo exports remain human_gold=false.
5. Settings/Coverage: inspect separate model roles, inactive generation/IE qualification, synthetic snapshot and unknown real coverage.

Owner acceptance is an actual person exercising these steps after resource recovery, then deciding on data/E1 rights, resident-model footprint/qualification, original-review access, independent people/freeze and any external pilot. Technical success does not grant scientific acceptance.

## Remaining engineering and qualification scope

Not claimed finished: authoritative free-text municipality resolver; broad pronoun/aspect/false-premise handling; controlled real-model synthesis comparisons; arbitrary graph neighborhood/time/type exploration against licensed corpus; qualified comparable multi-source time series/scatter/distribution charts; full regional/topic/temporal answerability coverage; ergonomic 400–1,000-object human workload study and full source/IE/answer-specific masks; comprehensive offline/cancellation/security/browser/assistive acceptance. Some require corpus/model/Human gates; independent implementation details remain a future iteration. This report does not label all unimplemented engineering as Human-only.
