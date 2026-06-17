# 6. CPT Implementation Decisions

Date: 2026-06-16

## Status

Accepted

## Context

Issue #8 tracks CPT implementation. ADR 005 defined the two-phase traversal design and segment matching. This ADR records the detailed implementation decisions from the CPT design session, covering the algorithm, data models, matching refactor, and basic resolution.

## Decisions

### 1. CPT Algorithm (revised)

CPT operates in four steps:

1. **Changed FQN**: Commit diff identifies entry points into the ADG.
2. **K-hop BFS**: Traverse structural edges (CONTAINS, CALLS, IMPORTS, INHERITS) both outward and inward from changed FQNs, up to k hops (default 3, tunable). Result: neighborhood `set[FQN]` + reachable `set[Edge]`.
3. **Constraint retrieval via neighborhood matching**: For each FQN in the k-hop neighborhood, check against constraint subject/object patterns using `fqn_matches_pattern`. Any constraint where subject or object matches something in the neighborhood is retrieved as relevant.
4. **Resolution**: For each retrieved constraint, check the predicate against structural facts from step 2 to determine pass/violation. Basic resolution handles specificity conflicts and deduplication.

### 2. Traversal: both directions, 3-hop limit

Both outward (changed FQN's actions) and inward (change's impact on dependents). 3 hops is the starting point, tunable based on observation.

### 3. `fqn_matches_pattern`: new matching primitive

Direction: FQN-first. Given a concrete FQN and a pattern string, return `MatchStatus`.

Matching layers (in order):
1. **Exact**: string equality
2. **Wildcard**: pattern like `app.api.*`, FQN is a child (standard prefix match)
3. **Segment (concrete)**: Jaccard overlap on dot-split segments, both non-wildcard, threshold >= 0.9
4. **Segment (wildcard)**: Jaccard on prefix segments (after stripping `.*`), plus verify FQN is a child of matched prefix
5. **No match**

Jaccard uses multisets (Counter), not sets, to preserve duplicate segments.

### 4. `match_fqn` refactor

`match_fqn` is refactored to build on `fqn_matches_pattern` internally. It iterates over ADG nodes, calls `fqn_matches_pattern` for each, and collects matches. One source of truth for matching logic.

### 5. Module location

`fqn_matches_pattern` lives in new `app/services/matching.py`, shared by CPT and merge layer.

### 6. Specificity formula (unchanged)

- EXACT: `depth(subject) + 1`
- WILDCARD: `depth(subject)`
- SEGMENT: `depth(subject) + jaccard_score`
- ORPHAN: 0.0

### 7. Segment threshold

Hardcoded constant `SEGMENT_THRESHOLD = 0.9`. Tunable by code edit; promote to config when value stabilizes.

### 8. Data models

```python
@dataclass
class Violation:
    constraint: ConstraintEdge
    changed_fqn: FQN          # entry point from diff
    matched_fqn: FQN           # neighborhood FQN that triggered the constraint
    match_status: MatchStatus  # how the FQN matched
    evidence: str              # structural fact that violates
    change_type: str           # "added" | "modified" | "deleted"

@dataclass
class CPTResult:
    violations: list[Violation]
    orphans: list[ConstraintEdge]   # constraints with no match
    neighborhood: set[FQN]          # for debugging/audit
```

### 9. Basic resolution

**Specificity conflict**: when PROHIBITS_DEPENDENCY vs REQUIRES_DEPENDENCY or PROHIBITS_IMPLEMENTATION vs REQUIRES_IMPLEMENTATION target the same object, higher specificity wins.

**Deduplication**: one violation per unique (constraint, matched_fqn) pair.

### 10. Orphan handling

Flat list in `CPTResult.orphans`, informational only. No filtering or blocking.

### 11. CPT has no MDS dependency

MDS is a post-CPT ranking layer, not a traversal prerequisite. CPT reads ADG in-memory; MDS is added in Phase 3.

## Consequences

- `match_fqn` refactor requires existing merge tests to pass unchanged
- New `app/services/matching.py` module is a shared dependency
- Segment matching adds complexity to matching logic but reduces orphan volume
- K-hop limit may miss deep transitive violations (tunable)
- Basic resolution only handles specificity conflicts and dedup; full R1-R4 resolution is separate