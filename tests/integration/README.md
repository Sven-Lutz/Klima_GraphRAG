# Disposable Neo4j projection and retrieval tests

The live projection contract runs only against the repository's ephemeral
Neo4j Community fixture. It uses tmpfs for graph data and accepts only a
loopback endpoint; `GRAPHRAG_NEO4J_TEST_DISPOSABLE=1` is the sole activation
switch and no production endpoint is ever accepted implicitly.

```bash
docker compose -f tests/integration/docker-compose.neo4j.yml up -d --wait
GRAPHRAG_NEO4J_TEST_DISPOSABLE=1 \
  pytest -m neo4j_integration tests/test_neo4j_projection_live.py
docker compose -f tests/integration/docker-compose.neo4j.yml down --volumes
```

`down --volumes` removes the container, the Compose network, and any volume the
service created. Graph data lives in tmpfs and disappears with the container.

The suite covers five live paths:

| Test | Live path |
|---|---|
| `..._clean_repeat_and_constraints` | Projection is idempotent; constraints and indexes exist. |
| `..._partial_failure_resume_and_scope_coexistence` | Interrupted batches resume; distinct scopes coexist. |
| `..._bounded_retrieval_resolves_sqlite_provenance` | Graph hits resolve back to authoritative SQLite evidence. |
| `..._hybrid_grounded_answer_path` | Hybrid retrieval feeds the canonical answer generator. |
| `..._canonical_answer_reaches_ui_contract` | Full path up to the FastAPI UI boundary and canonical export. |

The last test drives the complete chain: fixture SQLite snapshot → canonical
Neo4j projection → hybrid retrieval → authoritative SQLite evidence →
`CanonicalAnswerGenerator` → deterministic fake provider → `AnswerResult` →
`ResearchApplicationService` → FastAPI.

The default test suite skips all of these. Unit tests still exercise batch
atomicity, failure bookkeeping, resume, repeated reconciliation, snapshot
mismatch rejection, and distinct projection namespaces without a live service.
