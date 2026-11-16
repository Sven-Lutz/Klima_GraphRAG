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
