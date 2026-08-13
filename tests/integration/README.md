# Disposable Neo4j projection tests

The live projection contract runs only against the repository's ephemeral
Neo4j Community fixture. It uses tmpfs for graph data and accepts only a
loopback endpoint.

```bash
docker compose -f tests/integration/docker-compose.neo4j.yml up -d --wait
GRAPHRAG_NEO4J_TEST_DISPOSABLE=1 \
  pytest -m neo4j_integration tests/test_neo4j_projection_live.py
docker compose -f tests/integration/docker-compose.neo4j.yml down
```

The default test suite skips this service test. Unit tests still exercise
batch atomicity, failure bookkeeping, resume, repeated reconciliation,
snapshot mismatch rejection, and distinct projection namespaces.
