# ADR 001 — Evidence-bound V2 in the existing stack

Status: accepted for bounded technical preview, 16.11.2026.

Problem: extractive V1 lacks an explicit dialogue state, model-role boundary and standalone graph/data workspaces. Full-corpus integration depends on unresolved E1 rights. The target has 8 GB RAM and limited disk headroom.

Alternatives: migrate the full stack; replace the blocked E1 implementation; extend existing FastAPI/SQLite/JS contracts. Criteria: preservation of provenance/history, resource cost, rights, testability and incremental rollback.

Decision: extend the existing contracts, retain the extractive arm, introduce a separate optional generation adapter and keep scientific validation separate. No vendor substitution, new graph rebuild or model download. Scope is forwarded to capable real adapters; the controlled fixture backend cannot prefilter and explicitly records its bounded postfilter limitation. Free-text place names without authoritative resolution cannot yield fixture claims.

Evidence: existing service, CitationRegistry, Snapshot and ReviewProtocol contracts; unchanged historical studies. Technical test receipts are in V2_QA_REPORT.md. No empirical architecture or GraphRAG superiority inferred.

Costs: one serial worker; bounded evidence/context/output; optional local model weights <=1.5 GB. Existing 15/16-GiB gates unchanged. Markdown-it 14.1.0 is MIT licensed, locally distributed with its license; html/image rendering is disabled, link protocols validated, no CDN.

Risks: heuristic intent/context resolution is incomplete; model span/number/modal checks do not prove semantic entailment. A single-observation chart is not a time series. Demo identities are technical accounts, not independent people. Full registry/coverage qualification is gated.

Rollback: stop server and return to V1 commit in a separate checkout. No existing private DB/schema/index is migrated; V2 uses its own generated/chat-v2 directory. New turn fields are additive; old turn payloads remain readable. Never delete historical originals or review stores as rollback. Before any future migration, hash/backup affected artifacts separately.

Reconsider when: licensed corpus adapters and source rights are established, an independently qualified local model configuration exists, benchmark/split freezes are approved, and real user/assistive acceptance supplies evidence.
