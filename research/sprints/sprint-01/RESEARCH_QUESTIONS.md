# Research questions and falsifiable pilot hypotheses

Date: 2026-11-02. Status: study design, not empirical results. Inherits `research/planning/20261025_foundation_v01/FOUNDATION.md` and decisions D001–D006. Literature basis: `research/literature/sprint-01/CRITICAL_REVIEW.md`.

The target is auditable municipal climate-policy research across mitigation and adaptation. Publication, adoption, implementation, and measured effects are different states. Retrieval, fact extraction, relational analysis, multi-hop evidence assembly and corpus-wide synthesis require different outcomes. GraphRAG is a candidate. No hypothesis presupposes a winner or a national generalization from the present pilot.

| RQ | Observable research task | Primary outcome | Failure that would falsify proposed utility |
|---|---|---|---|
| RQ1 acquisition/identity | Find authoritative documents for a specified municipality and date | audited source coverage and attribution errors, with explicit inventory denominator | added pages fail to close independently specified evidence gaps or introduce wrong-jurisdiction facts |
| RQ2 conversion | Preserve numeric tables, headings, dates and source order | gold-span recoverability and semantic field preservation against original bodies/pages | reproducible conversion still loses or changes required facts |
| RQ3 extraction/representation | Record status, actor, measure, quantity/unit, reference period and jurisdiction | field-level correctness/completeness with provenance | structured store returns deterministic but missing/wrong records |
| RQ4 retrieval | Retrieve all necessary evidence for lookup and paraphrases | evidence Recall@k and Precision@k by task/source | a more complex retriever does not improve the paired outcome under common access/budget |
| RQ5 relation/reasoning | Connect plan, actor and dated action across source passages | complete supported evidence-bundle recovery and relation correctness | graph-added relations produce unsupported paths or offer no added valid evidence |
| RQ6 synthesis | Compare municipalities and explain historical changes | supported-claim completeness, comparability and unresolved-conflict disclosure | fluent synthesis mixes dates/definitions or overstates coverage |
| RQ7 verification/abstention | Detect absent, contradictory, ambiguous or wrong-municipality evidence | correct abstention/partial-answer behavior with answerable-case retention | refusal improves by refusing supported cases, or unsupported facts are asserted |
| RQ8 complexity | Repeat construction, update and query | cost/latency by stage and supported task outcome | apparent query saving disappears when build/update or failed runs are counted |

## Estimands and hypotheses for a later paired pilot

For a method A and prespecified comparator B, define Δ as the municipality-macro mean of within-task score(A) minus score(B), separately within each task family. Shared multi-municipality evidence creates linked clusters; it is not independent replication. For error rates use the same A−B orientation with lower better. The following are hypotheses, not accepted claims:

| ID | Intervention and outcome | Null | Alternative | Interpretation limit |
|---|---|---|---|---|
| H1 | semantic or hybrid vs lexical evidence Recall@k on paraphrased lookup | Δ ≤ 0 | Δ > 0 | compare precision and budget too; recall-only gain does not prove answer gain |
| H2 | explicit identity/time constraints vs unconstrained retrieval; wrong-scope error | Δ ≥ 0 | Δ < 0 | report missed valid cross-jurisdiction evidence; constraints can exclude true facts |
| H3 | corrected conversion vs inherited conversion; original-supported field accuracy | Δ ≤ 0 | Δ > 0 | fix corpus/version before method comparison; no silent source changes |
| H4 | structured extraction+SQL vs text baseline; numeric/temporal field accuracy | Δ ≤ 0 | Δ > 0 | extraction errors remain in denominator; SQL determinism is not completeness |
| H5 | graph addition vs same-source non-graph multi-step method; complete valid evidence bundles | Δ ≤ 0 | Δ > 0 | test only genuine multi-source relation tasks; include extraction/build cost |
| H6 | explicit abstention policy vs same retriever/generator without policy; unsupported assertion rate | Δ ≥ 0 | Δ < 0 | report answerable coverage separately; universal refusal is not success |

No fixed practical-effect or noninferiority margin has yet been justified by stakeholders or baseline variance. Zero is a directional hypothesis boundary, not a deployment threshold. The four-municipality pilot provides descriptive paired differences and examples; it does not support a powered confirmatory decision. Any future significance test, margin or aggregate utility score needs a separately frozen amendment before test outputs are inspected.

## Task taxonomy for annotation

T1 direct supported lookup; T2 numeric fact with unit/reference year; T3 historical change with compatible definitions; T4 cross-municipality comparison with matched concepts; T5 actor/measure relation; T6 multi-document evidence chain; T7 conflicting sources; T8 bounded-corpus unsupported or false-premise question; T9 ambiguous/wrong-municipality or modality-limited request. A task can have secondary tags; one linked question must not be counted as multiple independent observations. Corpus-wide synthesis requires an explicitly audited coverage frame and may remain unsupported by this pilot.

Tasks are admitted only when genuine sources and verification support their labels. The desired taxonomy does not authorize invented examples or a preset number of gold items. Existing E2 questions/outputs remain historical and their original labels are not overwritten.


Publication note (2026-11-15): original design text preserved. References to local Foundation, M2/M3, pilot-Gold decisions and historical data are historical dependencies not supplied or independently validated by this code publication. No review identities or reference answers are distributed.
