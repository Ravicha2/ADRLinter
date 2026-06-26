# 7. Naming Resolution Layer

Date: 2026-06-17

## Status

superceded by [ADR8](./008-symbolic-constraint-resolution.md)

## Context

E2E testing of CPT detection against a Flask repo revealed that ADR constraint extraction (via langextract) produces FQN patterns that don't match the ADG's structural nodes. Example: ADR says `app.api.*` but the codebase uses `app.routes`. The current matching layer (exact, wildcard, segment Jaccard) cannot bridge this gap, resulting in orphan constraints.

This is a two-part problem:

1. **Lexical substitution**: same-depth synonyms (`api` vs `routes`)
2. **Hierarchy errors**: LLM extracts wrong depth (`app.api` vs `app.auth.middleware`)

The LLM resolution layer supersedes the segment matching approach (multiset Jaccard). Since the LLM handles naming mismatches semantically, fuzzy string matching via Jaccard is no longer needed and is removed.

## Decisions

### 1. Resolution lives inside `merge_constraints`

The naming resolution step runs after orphan identification but before EXTERNAL node creation. After remapping, constraints are re-matched through the existing matching logic.

### 2. Candidate collection: prefix-scoped walk

Walk pattern segments against ADG nodes until the first mismatch. Collect all descendants of the longest matching prefix as candidates. If no segments match, candidates are the entire repo graph (bounded by repo size).

For `app.api.*` against a graph with `app.routes`, `app.models`, `app.services`:

- `app` matches → `api` doesn't → longest prefix is `app`
- Candidates: all nodes under `app.*`

### 3. One LLM call per orphaned side

If a constraint has both subject and object orphaned, two separate LLM calls. Reliability over cost.

### 4. LLM returns full pattern remapping

The LLM receives the orphaned FQN pattern, candidate FQNs, and the constraint's justification. It returns the full remapped pattern string (e.g., `app.routes.*`) or `"no_mapping"`.

### 5. In-place constraint modification

After successful remapping, the `ConstraintEdge.subject` or `object` field is replaced in-place with the remapped pattern.

### 6. No retry on failed remap

If the LLM returns `"no_mapping"` or the remapped pattern still doesn't match, the constraint stays an orphan.

### 7. Intentional orphans not differentiated

ADR constraints for code that doesn't exist yet (forward declarations) will sometimes get false remappings. No mitigation; accepted as known limitation.

### 8. Justification-only context for LLM

The resolution prompt uses the `justification` field from `ConstraintEdge`, not the full ADR text. Sufficient for naming resolution. Swapping to full ADR text is a trivial field change if needed later.

### 9. No node kind in prompt

The LLM prompt does not include `FQNKind` (MODULE, CLASS, etc.) for candidate nodes. Add later if resolution accuracy is insufficient.

### 10. Direct `openai` SDK call, shared config

Resolution uses `openai.OpenAI(base_url=..., api_key=...)` directly with `LangExtractConfig` values. No new dependency (`openai` is already available via `langextract[openai]`). No separate config.

### 11. Re-read ADR text from `adr_path` if needed

Current design uses justification only. If a fallback to full ADR text is added later, re-read from `ConstraintEdge.adr_path`. No caching; I/O cost is negligible for orphan-only calls.

### 12. Segment matching (Jaccard) removed

The LLM resolution layer supersedes the segment matching approach in `app/services/matching.py`. The Jaccard-based fuzzy matching (`_multiset_jaccard`, `SEGMENT_THRESHOLD`, `MatchStatus.SEGMENT`) is removed. Matching is now:

1. **EXACT**: string equality
2. **WILDCARD**: prefix match on `.*` patterns
3. __NO_MATCH__: nothing matched

`MatchStatus.SEGMENT` is removed from the enum. The segment case in `compute_specificity` is removed. Specificity simplifies to:

- EXACT: `depth(subject) + 1`
- WILDCARD: `depth(subject)`
- NO_MATCH/ORPHAN: 0.0

## Consequences

- Orphan count should decrease for repos where ADR terminology diverges from code structure
- Adds an LLM call per orphan per run; cost is bounded by orphan count (typically small)
- False positives on forward-declaration orphans are a known risk with no mitigation
- `merge_constraints` signature changes to accept LLM config or a resolution function
- Segment matching code (`_multiset_jaccard`, `SEGMENT_THRESHOLD`, `MatchStatus.SEGMENT`) is deleted
- `compute_specificity` simplifies: no Jaccard score parameter
- Existing segment-matching tests are rewritten for resolution-layer tests