# KIT KlimaCrawler — GraphRAG Pipeline Diagrams

Visual overview of the full index-time and query-time flows.

---

## 1. Index-Time Pipeline (Data Ingestion → Graph Construction)

```mermaid
flowchart TD
    subgraph CRAWL["🕷️ Stage 0 — Distributed Crawl"]
        S0A[seed_jobs table\nATOMIC municipality claim] --> S0B[Fetcher\npolite delay · robots.txt · Retry/Backoff]
        S0B --> S0C{Content-Type?}
        S0C -->|text/html| S0D[HTML Parser\nmeta tags · heading_context h1–h6\noutlinks → document_links]
        S0C -->|application/pdf| S0E[PDF Parser\nper-page sub-chunking\n≤3000 chars → paragraph split]
        S0D --> S0F[Scorer\nkeyword hit · heading boost\nis_negative filter]
        S0E --> S0F
        S0F --> S0G[(SQLite\nsegments · documents_raw\ndocument_links)]
    end

    subgraph CHUNK["📄 Stage 1 — Tiktoken Chunking"]
        S1A[(segments\nimpact_score ≥ threshold)] --> S1B[chunker.py\ncl100k_base\n600 tokens / 60 overlap]
        S1B --> S1C[(SQLite\nchunks table\nchunk_id · heading_context\nimpact_score · content identity)]
    end

    subgraph EMBED["🔢 Stage 2a — Vector Embeddings"]
        S2A[(complete chunk corpus)] --> S2B[SentenceTransformers\nexplicit model revision + preprocessing ID\nCUDA/MPS/CPU]
        S2B --> S2C[(embedding_artifacts + chunk_embeddings\ncontent-bound vectors + completion counts)]
    end

    subgraph GRAPH["🧠 Stage 2b — Graph Extraction (Ollama)"]
        S3A[(segments / chunks\nhigh impact_score)] --> S3B[extract_graph_ollama.py\nOllama llama3.1:8b\nformat=json]
        S3B --> S3C{Gleaning}
        S3C -->|Round 1| S3D[Initial extraction\nEntities + Relationships\nPydantic schema]
        S3D --> S3E[Self-reflection pass\n'Did you miss entities?']
        S3E --> S3F[Merge unique entities\nlowercase dedup]
        S3C -->|N rounds| S3E
        S3F --> S3G[(SQLite\ngraph_triplets)]
    end

    subgraph NEO4J["🗄️ Stage 3 — Canonical Neo4j Projection"]
        S4A[(manifested SQLite snapshot\nverified selected runs)] --> S4B[project_graph_to_neo4j.py\nstable projection namespace\nlocal lifecycle ledger]
        S4B --> S4C[(Neo4j\nArtifact · Evidence · Assertion\nMention · Entity · Source hierarchy)]
    end

    subgraph COMM["🔍 Community / Global Retrieval"]
        S5A[unsupported\nprojection-bound lifecycle and provenance deferred]
    end

    CRAWL --> CHUNK
    CHUNK --> EMBED
    CHUNK --> GRAPH
    EMBED --> S4A
    GRAPH --> S4A
    S4C -. future validated lifecycle .-> S5A
```

---

## 2. Query-Time Pipeline (Canonical Retrieval and Answer Generation)

```mermaid
flowchart TD
    Q["User query + explicit snapshot + mode"] --> V["Validate manifest and bounds"]
    V --> S["Semantic: compatible complete embedding artifact"]
    V --> G["Graph: verify projection/snapshot/run scope"]
    G --> C["Fixed parameterized 1-hop Cypher with cap/timeout"]
    C --> A["Resolve IDs to authoritative SQLite evidence"]
    S --> F["Deterministic rank or RRF fusion"]
    A --> F
    F --> D["Deduplicate by evidence_id; typed retrieval result"]
    D --> Z["Immutable citation IDs + ordered C1/C2 labels"]
    Z --> B["Deterministic whole-evidence budget"]
    B --> X["Versioned prompt + untrusted evidence boundary"]
    X --> P["Explicit generation provider/model; no fallback"]
    P --> GV["Structural citation grounding validator"]
    GV --> R["Typed AnswerResult + JSON/Markdown"]
```

---

## 3. Evaluation Pipeline (Stage 5)

```mermaid
flowchart LR
    subgraph AutoQ["AutoQ — Synthetic Question Generation"]
        AQ1[community_reports\nSQLite] --> AQ2[generate_local_questions\nfactual, entity-specific]
        AQ1 --> AQ3[generate_global_questions\nthematic, cross-community]
        AQ1 --> AQ4[generate_aggregation_questions\ncounting, comparison]
        AQ2 & AQ3 & AQ4 --> AQ5[TestQuestion list\nJSON export]
    end

    subgraph QA["QA — Answer Generation"]
        AQ5 --> QA1[canonical grounded answer API\nimplemented; evaluation adapter deferred]
        QA1 --> QA2[EvalResult\nquestion · answer · contexts\nroute_used · latency_s]
    end

    subgraph RAGAS["RAGAS Metrics"]
        QA2 --> R1[score_faithfulness\nLLM-as-judge]
        QA2 --> R2[score_context_recall\nLLM-as-judge]
        QA2 --> R3[claimify\natomic claim decomposition\nper-claim verification]
        R1 & R2 & R3 --> R4[per-question scores\nfaithfulness · context_recall\nclaim_accuracy]
    end

    subgraph AutoE["AutoE — Head-to-Head Comparison"]
        SYS_A[System A answers] --> AE1[autoe_compare\nLLM judge]
        SYS_B[System B answers] --> AE1
        AE1 --> AE2[winner per dimension\nComprehensiveness\nDiversity\nFaithfulness]
    end

    RAGAS --> RPT[Evaluation Report\nJSON + console summary\nmean scores per route type]
    AutoE --> RPT
```

---

## 4. Legacy downstream Neo4j schema (not compatible with canonical projection)

The following diagram describes the pre-projection community/query modules.
They are not enabled for the canonical model. See
[`NEO4J_PROJECTION.md`](NEO4J_PROJECTION.md) for the implemented schema.

```mermaid
graph LR
    D[Document] -->|HAS_SEGMENT| S[Segment\nimpact_score\nheading_context\norder_index]
    D -->|HAS_CHUNK| C[Chunk\nembedding float32\ntoken_count\nimpact_score]
    D -->|LINKS_TO| D2[Document]

    S -->|MENTIONS| E[Entity\ntype · category · status\nmetrics dict\ncommunityId]
    E -->|FÖRDERT\nBAUT\nBESCHLIESST\nIMPLEMENTIERT\nPLANT\nGEHÖRT_ZU\nKOOPERIERT_MIT\nFINANZIERT_DURCH\nBEZIEHT_SICH_AUF| E2[Entity]

    E -->|BELONGS_TO_COMMUNITY| CR[CommunityReport\ntitle · summary\nimportance 1–10\ndomain · key_claims]
```

---

## 5. Running the Full Pipeline

```bash
# 0. Crawl municipalities (distributed)
python -m crawler.scripts.init_seed_jobs
python -m crawler.scripts.run_worker

# 1. Tiktoken chunking
python -m crawler.scripts.chunk_documents --chunk-size 600 --overlap 60 --min-score 15

# 2a. Generate embeddings (local GPU)
python -m crawler.scripts.generate_embeddings --model paraphrase-multilingual-MiniLM-L12-v2

# 2b. Extract entity graph (Ollama, privacy-compliant)
python -m crawler.scripts.extract_graph_ollama --model llama3.1:8b --gleaning 1 --chunk-mode

# 2c. Create a snapshot, then project selected completed extraction run IDs.
python -m crawler.scripts.create_database_snapshot --db crawler/data/db/crawl.sqlite \
  --output snapshots/crawl.sqlite
# See docs/NEO4J_PROJECTION.md for the explicit projection command.

# Community detection and query are not yet migrated to the canonical graph.

# 5. Scientific evaluation
python -m crawler.scripts.evaluate --questions 30 --output eval_results.json

# Utilities
python -m crawler.scripts.resolve_entities          # Entity deduplication
python -m crawler.scripts.summarize_documents       # Per-document LLM summaries
python -m crawler.scripts.analyze_topics            # BERTopic topic modelling
```
