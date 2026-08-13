# Interactive research application

Status: **implemented** for local, snapshot-bound semantic, graph, and hybrid
research workflows. The live generation provider gate is **closed**: the
canonical `OllamaGenerationProvider` was validated against a real Ollama service
(see [Provider acceptance versus runtime availability](#provider-acceptance-versus-runtime-availability)).
Acceptance is a project record and is never treated as proof that a provider is
reachable during a given session.

## Architecture boundary

The application is intentionally a consumer of canonical backend contracts:

```text
Browser presentation (HTML/CSS/JavaScript)
  -> FastAPI HTTP boundary
  -> ResearchApplicationService
  -> CanonicalRetriever / CanonicalAnswerGenerator
  -> immutable RetrievalResult / AnswerResult
```

The browser contains no retrieval, ranking, grounding, provider, SQL, Cypher,
citation-identity, or provenance-reconstruction logic. `ResearchApplicationService`
constructs typed requests and projects canonical results for inspection without
mutating them. Exports call the canonical Markdown and JSON renderers. A session
store retains the complete immutable result object associated with each export ID.

FastAPI was selected because the repository is Python-native and needs typed,
testable HTTP boundaries and safe exception handling. Server-rendered static HTML,
CSS, and small dependency-free JavaScript avoid a separate Node build system.

## Controlled fixture mode

Create a Python 3.11 environment, install the declared development dependencies,
and start the local fixture:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m crawler.web --fixture --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Fixture mode generates its own disposable source
database, canonical embedding artifact, manifested snapshot, graph projection
plan, and answer-run log in an isolated temporary directory. It never reads
`crawler/data/db/crawl.sqlite`. The banner marks every fixture result as controlled
test data rather than real municipal research data.

The fixture exercises:

```text
generated SQLite source
  -> manifested snapshot
  -> CanonicalRetriever (semantic / graph / hybrid)
  -> CanonicalAnswerGenerator
  -> deterministic fixture provider
  -> AnswerResult
  -> application adapter and browser
```

## Configured snapshot mode

The application fails closed unless the database is the exact SQLite file bound
by the manifest. The answer-run log parent directory must already exist.

```bash
mkdir -p generated/answer-runs
export KLIMA_UI_SNAPSHOT_MANIFEST=/absolute/path/snapshot.sqlite.manifest.json
export KLIMA_UI_DB_PATH=/absolute/path/snapshot.sqlite
export KLIMA_UI_ANSWER_RUN_LOG=$PWD/generated/answer-runs/ui.jsonl
.venv/bin/python -m crawler.web --host 127.0.0.1 --port 8000
```

Semantic retrieval also uses the embedding settings whose defaults match the
canonical embedding CLI. The installed local model must match the snapshot's
embedding lineage. Model downloads are not performed by the UI.

Graph and hybrid retrieval additionally require an exact projection and a
dedicated read-only Neo4j principal:

```bash
export KLIMA_UI_NEO4J_ENABLED=1
export KLIMA_UI_PROJECTION_ID=neo4j-projection-sha256:...
export KLIMA_UI_EXTRACTION_RUN_IDS=run-id-1,run-id-2
export KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED=1
export KLIMA_RETRIEVAL_NEO4J_URI=bolt://127.0.0.1:7687
export KLIMA_RETRIEVAL_NEO4J_DATABASE=neo4j
export KLIMA_RETRIEVAL_NEO4J_USER=graphrag_retrieval_reader
export KLIMA_RETRIEVAL_NEO4J_PASSWORD='set-outside-shell-history'
```

The UI never displays the password, raw connection string, or a backend stack
trace. Neo4j connectivity is not needed for semantic retrieval.

## Answer generation and provider readiness

Retrieval-only use is the default configured state. To configure Ollama answer
generation explicitly:

```bash
export KLIMA_UI_GENERATION_ENABLED=1
export KLIMA_OLLAMA_URL=http://127.0.0.1:11434
export KLIMA_OLLAMA_MODEL=llama3.1:8b
```

A timeout or unavailable provider yields a typed canonical generation-failure
result while retrieved evidence remains inspectable. The UI does not attempt
fallback providers.

## Provider acceptance versus runtime availability

`ProviderGate` deliberately separates two things that earlier revisions of this
document conflated.

**Acceptance** (`acceptance`, `acceptance_evidence`) is an immutable project
record describing whether the provider adapter was validated against a real
service under the canonical contract. Two values exist:

| Value | Meaning |
|---|---|
| `live_provider_validated` | The canonical Ollama adapter was validated live. Ollama 0.32.5 with `llama3.1:8b` produced a successful direct generation and a successful canonical `OllamaGenerationProvider` generation, with exact requested/served model identity, valid response parsing, token metadata, and a reported finish reason. |
| `fixture_provider_deterministic` | The composition uses the deterministic in-process fixture provider. It is independent of Ollama and asserts nothing about any live service. |

The earlier `open_live_provider_timeout` and `fixture_only_live_provider_open`
values are removed. The timeouts that produced them were caused by local memory
pressure and cold-start thrashing on an 8 GiB Apple M3 host, not by a repository
defect.

**Runtime availability** (`runtime`, `runtime_message`) is observed per process
and derived exclusively from canonical `AnswerResult` outcomes:

| Value | Derived from |
|---|---|
| `not_configured` | No generator, or generation is not enabled in this composition. |
| `not_probed` | Generation is configured; no generation has been attempted yet in this process. |
| `available` | The most recent attempt reached the provider (`answered` or `grounding_failed`). |
| `timeout` | The most recent attempt failed with `provider_timeout`. |
| `unavailable` | The most recent attempt failed with `provider_unavailable`. |
| `runtime_failure` | The provider responded but the response was unusable (malformed, empty, or rejected configuration). |

A closed acceptance gate never sets `runtime` to `available`. A freshly
configured production composition reports `not_probed` until a real generation
attempt is made, and `insufficient_evidence` leaves the state unchanged because
the canonical layer can reach it without calling the provider at all.

Cold model loading is not a separate observable state: the Ollama chat API
blocks while a model loads and exposes no distinct loading signal, so a cold
start surfaces as a retryable `timeout`. The browser shows a transient
"Contacting provider…" indicator while a generation request is in flight, and
the sidebar notes that a first cold load can take a while.

Fixture mode is a fully independent path. It uses the deterministic fixture
provider, never contacts Ollama, and its `available` runtime state says nothing
about live provider health.

## Research workflow

1. Enter a question and choose semantic, graph, or hybrid retrieval.
2. Set canonical top-k and answer-context item/character budgets.
3. Disable answer generation for retrieval-only investigation when desired.
4. Inspect answer status, grounding status, inline citation links, ranked evidence,
   source/document hierarchy, municipality context, and canonical source URLs.
5. Open the Graph tab to inspect stable entity/assertion/evidence/document identity.
   Connectivity is explicitly described as a derived navigation aid, not factual proof.
6. Open Trace for semantic, graph, and reciprocal-rank-fusion contributions, final
   rank, deduplication identity, snapshot, projection, and run scope.
7. Export the immutable current result through canonical Markdown or JSON renderers.

The evidence detail dialog distinguishes authoritative source evidence from derived
graph assertions. Canonical URLs are linked only when supplied by the result.
Closing the dialog returns to the same result and query state.

## Accessibility and interaction

Controls have visible labels, semantic roles, keyboard focus indicators, status
announcements, and non-color status text. `Command/Ctrl+Enter` executes a query.
Tabs, citations, evidence cards, graph nodes, and graph edges are keyboard usable.
The layout reflows for narrow screens and honors reduced-motion preferences.

## Error and empty states

The application explicitly represents no query, no evidence, insufficient
evidence, provider unavailable, provider timeout, generation failure, grounding
failure, snapshot mismatch, Neo4j unavailable, malformed configuration, and safe
backend failure. Rejected provider text is never styled as a grounded answer.

## Validation and limitations

Focused tests use the real canonical fixture path. The disposable Neo4j suite
(`tests/test_neo4j_projection_live.py`, opt-in via `GRAPHRAG_NEO4J_TEST_DISPOSABLE=1`)
remains the live integration gate and includes
`test_disposable_neo4j_canonical_answer_reaches_ui_contract`, which drives a live
Neo4j 5 projection through hybrid retrieval, the canonical answer generator, and
the FastAPI boundary up to the exported canonical JSON. The UI supports local single-process
research sessions; its immutable in-memory export store is not a multi-user history
service. Municipality/source filtering currently filters the returned evidence for
inspection and does not alter canonical retrieval semantics. Global/community
retrieval, semantic-faithfulness guarantees, and production readiness remain
**unsupported**.

![Fixture research workflow](assets/research-ui-fixture.jpg)
