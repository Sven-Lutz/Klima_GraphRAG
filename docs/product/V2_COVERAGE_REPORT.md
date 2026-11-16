# Local coverage and model inventory — 16.11.2026

Engineering inventory, not independent scientific validation. No original text, model weights or personal judgments are distributed. Historical 4,562/176,075 snapshot figures remain historical.

## Source inventory

Owner-local manifest `product-followup-20261113-network/workspace.json`: SHA256 `10b3cb84fa61da4069ba403e2aeddd7ffe2413e3748b8fdf665912423c0237a7`, snapshot `bavaria-workspace-sha256:64710fca1414d8c6ba9ad0f5046fa45224f61768a14843af6da36e56b75040ed`. All five bound artifact hashes match; quick_check on the four acquisition ledgers is ok. The reproducible script also checks the relations DB and post-inventory identities. No source database was created or migrated.

Read-only **distinct-ID union**, without replaying federation disposition/precedence: frame 2,056; request-associated municipalities 2,052; document IDs 4,564; unit IDs 176,176; municipalities associated with text 1,733. These are inventory counts, not qualified authoritative sources, distinct independent document families, indexed topic coverage or municipal answerability. Base ledger alone: 4,494 documents, 173,294 units; parse statuses 4,382 TEXT_EXTRACTED, 55 EMPTY_OR_PARSE_FAILED, 57 INHERITED_E2_SOURCE_ORDER. The newer union and historical snapshot must not be pooled.

Reproduce locally with the explicit owner manifest:

```bash
.venv/bin/python scripts/qa/inventory_conversation_corpus.py --manifest /absolute/owner/workspace.json
```

The script verifies hashes before/after, opens source DBs with mode=ro, validates required schema and produces only bounded aggregate metadata on stdout. Missing DB/hash/schema fails closed. No E1 import or substitute retriever.

| Coverage dimension | Status |
|---|---|
| Municipality in frame | 2,056 in the bound base ledger |
| Acquisition attempted | 2,052 with request rows in the union; not complete acquisition success |
| Authoritative sources | NOT_INDEPENDENTLY_QUALIFIED |
| Original available | NOT_VERIFIED_PER_ARCHIVE_FILE |
| Parsed | Unit inventory above; parser correctness not established by counts |
| Indexed | Existing private artifacts observed; no rebuild/current full-index qualification |
| Topic evidence | NOT_HUMAN_QUALIFIED |
| Claim scope | NOT_INDEPENDENTLY_QUALIFIED |
| Human reviewed | NOT_ESTABLISHED |
| Answerability audited | NOT_EVALUATED |

Regional/county/topic/time/source-type strata and shared jurisdiction must be qualified from licensed records before public integration. No inferred all-Bavaria completeness. E1 rights and municipal original redistribution/viewing scope remain unresolved. Public runtime uses only the controlled synthetic snapshot. Private index/source payloads are not copied.

## Models and resources

Ollama CLI 0.13.5. Manifest identities observed locally:

| Tag | Manifest SHA256 | Nominal model bytes | Actual availability |
|---|---|---:|---|
| qwen3:0.6b | 7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435 | 522,640,096 | Nonresident placeholder; 0 allocated blocks |
| qwen3:1.7b | 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7 | 1,359,279,776 | Nonresident placeholder; 0 allocated blocks |
| qwen2.5:3b | 357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b | 1,929,903,008 | Nonresident placeholder; 0 allocated blocks |

Manifest hashes are not freshly verified model-blob digests. macOS flags 1073741920 and st_blocks=0 were observed on all three model blobs. The 0.6B read/hash attempt stalled and was interrupted; no successful blob verification or inference. A short-lived loopback Ollama server with NOPRUNE=1, KEEP_ALIVE=0, one queue/worker and requested CPU library was stopped after its tag API timed out. Startup still reported Metal discovery; no CPU execution claim. No ollama pull, no deliberately initiated model download and no paid API call. Any filesystem cloud materialization triggered by reading was not verified as completed; no resident blob was established.

Physical RAM 8,589,934,592 bytes, memory_pressure query 31% available at one observation, encrypted swap used approximately 6,929 MiB. This is not a model RAM benchmark. Existing disk gates remain; final free-space readings are in QA receipts. Qualification and rights for an actual model run remain open. IE historical negative results remain unchanged; no broad extraction release, graph rewrite or experiment overwrite.

## München heat evidence follow-up

Bounded read-only keyword query on units joined to associations with AGS 09162000: base/delta/temporal ledgers each have 0 matching `hitze` units; the newer network-follow-up ledger has 36 (34 + 2 across two source hashes), including 2 `hitzeschutz` matches and 0 exact `hitzevorsorge` matches. These are query-specific string counts, not relevance or answerability labels.

Stored source metadata identifies `stadt.muenchen.de/infos/hitze-gesundheit.html` (SHA256 `5453d57fe336f46e354553fc0779ed75d65793a512ec3064fc68dc9d6a5cc591`) and a `ru.muenchen.de` Hitzeschutz entry (SHA256 `d08b1dc9849de5ef7e0d50e00505ccc89f0e57057cb90b380bca94ffc7fa3563`). Both are recorded as OFFICIAL_DOMAIN_CANDIDATE_NOT_HUMAN_REVIEWED. No current live-web verification, original-understanding claim or independent scope qualification. Historical evidence gaps remain historical; the newer candidates prevent carrying the old absence statement forward unchanged. This does not establish that the product can answer a specific heat-policy question. No unrelated municipality was substituted, no new crawl performed.
