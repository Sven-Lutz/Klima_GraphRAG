# AGENTS.md

## Project Objective

Develop this repository into a technically excellent, robust, reproducible,
well-documented, and professionally presented GraphRAG system.

Do not optimize merely for minimal task completion.

Actively improve:

- architecture
- correctness
- retrieval quality
- graph quality
- maintainability
- performance
- usability
- developer experience
- testing
- evaluation
- reproducibility
- documentation
- repository presentation

Creative, ambitious, and unconventional solutions are encouraged when they
provide a meaningful technical benefit.

Clearly distinguish between:

1. required corrections
2. recommended improvements
3. experimental ideas

For substantial proposals, explain the expected benefit, implementation cost,
risks, and validation strategy.

---

## Autonomous Repository Work

Codex is authorized to work autonomously within this repository.

Codex may:

- inspect all repository files
- create new files and directories
- edit existing files
- move and rename files
- remove obsolete files after verifying that they are no longer required
- refactor architecture and implementation
- add or remove dependencies when technically justified
- run tests, linters, formatters, type checks, benchmarks, and build tools
- create and update documentation
- improve README.md and public repository presentation
- create local Git commits
- reorganize local implementation work into coherent commits
- propose and implement creative technical solutions

Do not request confirmation for routine repository-local edits.

For large or high-risk architectural changes:

1. inspect the existing implementation
2. explain the identified limitation
3. create a concise implementation plan
4. preserve a recoverable Git state
5. implement and validate the change

Prefer robust systemic improvements over fragile local patches.

---

## External and Irreversible Actions

Explicit authorization is required before:

- pushing commits
- force-pushing
- publishing branches
- opening or merging pull requests
- creating or publishing releases
- creating or pushing tags
- modifying remote repository settings
- changing the GitHub About description or topics remotely
- accessing production systems
- modifying external databases
- using or modifying real credentials
- deleting remote resources
- rewriting published Git history

Codex may prepare proposed GitHub About text, repository descriptions, topics,
release notes, and metadata locally without separate authorization.

---

## Creative Engineering

Actively consider improvements such as:

- alternative GraphRAG architectures
- hybrid retrieval
- semantic and graph-based retrieval combinations
- reranking
- adaptive chunking
- entity resolution
- relation extraction improvements
- graph deduplication
- confidence estimation
- citation and provenance tracking
- evaluation datasets
- automated retrieval evaluation
- hallucination detection
- caching
- parallelization
- observability
- visualization
- modular provider interfaces
- improved command-line or user interfaces

Experimental proposals should define:

- hypothesis
- expected benefit
- implementation scope
- evaluation method
- failure criteria
- fallback strategy

Do not present hypotheses as measured results.

---

## Architecture and Implementation

Maintain explicit boundaries between:

- ingestion
- preprocessing
- chunking
- entity extraction
- relation extraction
- entity resolution
- graph construction
- graph persistence
- embedding generation
- indexing
- retrieval
- reranking
- context construction
- answer generation
- citation handling
- evaluation
- configuration
- user interfaces

Prefer:

- explicit interfaces
- typed data structures
- cohesive modules
- configuration-driven behavior
- deterministic execution where feasible
- small and testable functions
- structured logging
- meaningful error messages
- documented extension points

Avoid:

- hidden coupling
- duplicated business logic
- unnecessary global state
- hard-coded environment assumptions
- silent failures
- broad exception swallowing
- abstractions without concrete value

Weak existing architecture does not need to be preserved merely because it
already exists.

---

## Documentation

Documentation is a first-class project component.

Keep documentation synchronized with implementation changes.

Maintain at the highest practical professional standard:

- README.md
- installation instructions
- quick-start guide
- architecture documentation
- configuration reference
- usage examples
- API documentation
- evaluation methodology
- troubleshooting guide
- changelog
- roadmap
- diagrams
- screenshots
- example outputs
- limitations
- repository description
- proposed GitHub About text
- proposed repository topics
- release documentation

The README should immediately explain:

1. what the project does
2. why it matters
3. its main capabilities
4. its current maturity
5. how to run a minimal example

Documentation must remain:

- technically accurate
- professionally written
- easy to navigate
- consistent in terminology
- reproducible
- free of placeholders
- free of unsupported claims
- aligned with the actual source code

Clearly separate:

- implemented features
- experimental features
- planned features
- research ideas

Do not claim production readiness unless it has been demonstrated.

---

## Testing and Evaluation

Every bug fix should include a regression test where feasible.

Every significant feature should include appropriate validation.

Use the relevant combination of:

- unit tests
- integration tests
- end-to-end tests
- static type checking
- linting
- formatting
- schema validation
- graph consistency checks
- retrieval evaluation
- answer-quality evaluation
- citation verification
- performance benchmarks
- reproducibility checks

For GraphRAG functionality, consider measuring:

- retrieval precision
- retrieval recall
- context relevance
- answer faithfulness
- citation correctness
- entity extraction quality
- relation extraction quality
- duplicate entity rate
- graph connectivity
- unsupported relation rate
- latency
- token consumption
- embedding cost
- index construction time

Never hide failing checks.

When validation cannot be completed, report:

- which check was not executed
- why it was not executed
- which risk remains

---

## Git Workflow

Codex may create local Git commits autonomously.

Create commits for coherent and understandable units of work, including:

- complete bug fixes
- feature increments
- architectural refactorings
- documentation milestones
- testing improvements
- evaluation improvements
- repository presentation improvements

Before each commit:

1. inspect the complete diff
2. remove accidental changes
3. run relevant checks
4. update affected documentation
5. verify that no secrets were introduced
6. verify that the commit represents a coherent change

Use clear and professional commit messages.

Avoid:

- meaningless micro-commits
- unrelated changes in one commit
- knowingly broken intermediate states
- generated clutter
- secrets
- unnecessary binary artifacts

Codex may use the currently configured local Git identity during development.

Do not add unnecessary attribution such as:

- Co-authored-by trailers
- Generated-by trailers
- assistant attribution
- AI attribution in source files or documentation

The complete author and contributor history may be normalized separately before
the repository is finalized or publicly released.

Do not rewrite published history without explicit authorization.

Do not push anything without explicit authorization.

---

## Repository Quality

Continuously detect and improve:

- dead code
- duplicate implementations
- unused dependencies
- obsolete experiments
- stale documentation
- broken commands
- inconsistent naming
- missing configuration examples
- unclear module boundaries
- environment-specific paths
- accidentally committed generated files
- missing licenses or dependency notices
- weak onboarding
- incomplete examples

Do not delete potentially valuable work without first verifying its purpose and
preserving it through Git history.

---

## Completion Standard

Before completing a substantial task, verify:

- the intended behavior works
- architecture remains coherent
- edge cases are addressed
- relevant tests pass
- type checking and linting remain valid
- documentation is synchronized
- setup instructions remain reproducible
- examples remain functional
- no secrets were introduced
- no unsupported claims were added
- the final diff contains only intentional changes

Conclude substantial tasks with:

1. implemented changes
2. files changed
3. architectural decisions
4. validation performed
5. commits created
6. remaining limitations
7. recommended next steps

---

## GraphRAG_AL Repository-Specific Rules

### Database consistency

- Every pipeline command and library entry point must receive one explicit
  database path, either through a required argument or one resolved runtime
  configuration object.
- A CLI `--db` value is authoritative for the entire invocation. Do not mix it
  with a database path captured from environment variables at import time.
- Database selection must be emitted in structured run metadata and be covered
  by tests that verify every stage reads and writes the same database.
- Never create an implicit fallback database when an explicitly selected
  database is unavailable. Fail with an actionable error instead.

### Graph provenance

- Every extracted entity and relationship must retain a source type, source
  identifier, document identifier, municipality identifier, extraction model,
  prompt version, and sufficient source context to reconstruct a citation.
- Source types and identifiers must be explicit; do not overload a
  segment-specific field with chunk identifiers or other row IDs.
- SQLite persistence, Neo4j export, retrieval, answer context construction,
  and citation rendering must preserve the same provenance contract without
  lossy remapping.
- Add integration tests for the complete source-to-citation path for both
  segment and chunk extraction modes before changing this contract.

### Text2Cypher security

- Text2Cypher must use a dedicated read-only Neo4j principal with the minimum
  required graph privileges; never use an administrative or write-capable
  account for LLM-generated queries.
- Validate generated Cypher against an explicit, documented read-query
  allowlist before execution. Reject unknown procedures, mutations, schema
  operations, file/network access, and multi-statement queries.
- Enforce server-side and client-side query timeouts, bounded result sizes, and
  a safe error response that does not expose credentials or internal topology.
- Never execute an LLM-generated mutation query, including `CREATE`, `MERGE`,
  `SET`, `DELETE`, `REMOVE`, `DROP`, `CALL` outside an allowlist, or equivalent
  operations.

### LLM run metadata

- Persist a run record for every extraction, summarization, community-report,
  answer, and evaluation operation.
- Each run record must include provider, model identifier, prompt version,
  parameters, seed when supported, database snapshot or manifest identifier,
  start/end timestamps, input identifiers, output identifiers, and processing
  status (`pending`, `running`, `succeeded`, `failed`, or `skipped`).
- Persist failures with a safe, actionable error classification; do not mark
  failed inputs as complete or silently discard them.

### Pipeline validation gates

- Each stage must validate its required schema version, database identity,
  source-reference integrity, embedding dimensions and coverage, required
  services, and predecessor-stage completeness before processing.
- A stage must write an explicit completion record only after successful
  validation and processing. Partial work must remain distinguishable from a
  complete stage.
- Missing prerequisites, invalid provenance, unavailable services, or
  incomplete predecessor output must fail explicitly rather than silently
  propagating incomplete state.

### Generated artifacts

- Classify every crawl database, raw crawl payload, model file, log,
  visualization, evaluation output, and demo artifact as versioned, ignored,
  externally managed, or generated and reproducible.
- Generated artifacts must not be committed unless they are intentionally
  versioned fixtures or release assets with documented provenance and size
  justification.
- Large artifacts require a manifest or documented snapshot mechanism that
  records origin, schema/version, checksum where practical, creation command,
  and restoration procedure.
- Keep generated outputs out of source directories where practical, and keep
  ignore rules narrow enough that future source assets are not hidden.

### Quality objectives

- Establish a measured baseline before setting target values. Until then,
  record metrics and use explicitly documented warning thresholds only where
  they are evidence-based.
- Track retrieval quality, graph connectivity, duplicate-entity rate,
  unresolved-relation rate, citation coverage, answer faithfulness, latency,
  token usage, and estimated provider or compute cost for relevant runs.
- Reports must identify dataset, database snapshot, model, prompt version, and
  evaluation method. Never present synthetic or LLM-judge scores as ground
  truth without qualification.

### Documentation maturity

- Mark capabilities consistently as `implemented`, `experimental`, `planned`,
  `deprecated`, or `unsupported` in user-facing documentation.
- Do not describe evaluation, SQLite-only operation, Neo4j support, or
  production readiness more strongly than verified implementation and tests
  justify.
- Commands in README, pipeline, demo, and HPC documentation must be tested or
  clearly marked as unverified environment-specific instructions. Remove or
  correct stale commands in the same change that makes them stale.
