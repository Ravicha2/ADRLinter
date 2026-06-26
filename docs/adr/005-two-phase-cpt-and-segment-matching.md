# 5. Two-Phase CPT Traversal and Segment Matching for Constraint Resolution

Date: 2026-06-12

## Status

Accepted (Phase 1 k-hop BFS superseded by [ADR 009](./009-sound-cpt-reachability.md))

## Context

After running the seed-build pipeline and visualizing the merged ADG in Neo4j, constraint edges form disconnected subgraphs (e.g., `app.routes.* -[PROHIBITS_DEPENDENCY]-> app.models.*`) that are unreachable from the main AST component via structural edges. This raised the question: how can CPT detect violations if it cannot traverse from a changed FQN to its associated constraints?

Additionally, the existing `match_fqn` function had only two layers (exact and wildcard), causing near-miss FQNs from LLM extraction (e.g., `app.auth.middleware` vs `app.middleware.auth`) to fall to ORPHAN/R4. The original Phase 1 plan deferred suffix/segment matching, but the near-miss pattern is now observable and solvable.

## Decision

### 1. Two-phase CPT traversal (no graph connectivity needed)

CPT operates in two phases rather than requiring a connected graph:

- **Phase 1 (AST walk):** Traverse structural edges (CONTAINS, CALLS, IMPORTS, INHERITS) from changed FQNs. Collect which FQNs are reachable and what structural relationships exist.
- **Phase 2 (Constraint evaluation):** Evaluate each constraint edge independently by expanding wildcard and segment-matched subjects/objects against the full AST node set, then check for violations using Phase 1's structural facts.

No `EXPANDS_TO` edges, storage-time expansion, or schema changes are needed. The constraint subgraph stays disconnected from the AST graph by design.

### 2. Segment matching as Layer 3 of `match_fqn`

Added a third matching layer between wildcard and orphan:

1. **Exact match:** string equals `str(FQN)` for some node
2. **Wildcard expansion:** pattern ends with `.*`, expand against known FQNs
3. **Segment matching:** Jaccard overlap on FQN segments (split by `.`), threshold 0.9-1.0
4. **Orphan:** no match found, flagged as informational

Algorithm: split both the pattern and candidate FQN by `.`, compute `|intersection| / |union|` of segment sets. If the Jaccard score meets the threshold, the candidate is a match. This primarily catches segment reorders (`app.auth.middleware` vs `app.middleware.auth`).

Segment matches are folded into the EXACT MatchStatus. The distinction is visible in Neo4j where the original FQN string on the constraint edge differs from the matched node's FQN.

Threshold starts at 0.9-1.0 and is tunable based on observation. At depth 2-3 (typical FQN depth), different-length FQNs almost never achieve 0.9+ overlap, which is correct: different segment counts likely mean different semantics.

### 3. Specificity formula updated

```
specificity = depth(subject) + match_bonus - 0.5 * wildcard_count(subject)
```

Where `match_bonus`:
- EXACT match: 1
- Segment match (folded into EXACT): jaccard_score (0.9-1.0)
- WILDCARD match: 0
- ORPHAN: specificity = 0.0 regardless

A perfect segment reorder (jaccard=1.0) yields the same specificity as an exact match, which is idempotent: same segments in different order have the same narrowing power.

### 4. R4 for orphan FQNs is informational, not blocking

Genuinely orphan FQNs (no exact, wildcard, or segment match) are surfaced as informational flags for human review ("ADR references FQN not in codebase"). They do not block the pipeline. Hallucinated FQNs (e.g., `app.api.*` where no `app.api` exists) are an extraction quality problem, not a matching problem.

## Consequences

- Orphan subgraphs in Neo4j are expected and do not need resolution.
- CPT implementation can be simpler: two independent data sources evaluated together, no complex graph traversal across components.
- Near-miss FQNs from extraction are now resolvable, reducing R4 flag volume.
- The specificity formula needs updating in `compute_specificity` to accept a match_bonus parameter.
- The `match_fqn` function needs a new matching layer between wildcard and orphan.
- Threshold tuning requires observation against real extraction data.