# 10. Replace/Supercede Pattern: Extraction and Resolution

Date: 2026-06-27

## Status

Accepted

## Context

ADRs that prescribe a technology replacement (e.g., "Replace Flask with Django" or "Switch to PostgreSQL") produce two conflicting constraints: a `prohibits_dependency` on the old technology and a `requires_dependency` on the new one. Without special handling, CPT fires violations for both sides of the replacement, including spurious `requires_dependency` violations in code that has not yet migrated.

The current resolution tier (`suppress_outweighed_prohibits`, ADR 008 section 9) only suppresses `prohibits_*` violations when a higher-specificity `requires_*` constraint covers the same object. The symmetric case (a `prohibits_*` outweighing a `requires_*`) is missing.

Additionally, the extraction prompt had no rule for "Replace X with Y" phrasing, so the LLM would either emit a single `prohibits_dependency` (missing the new-technology requirement) or a single `requires_dependency` (missing the old-technology prohibition), producing incomplete constraint pairs.

## Decision

### 1. Extraction: Replace/Supercede pattern rule

The extraction prompt now includes rule 3:

> **REPLACEMENT / SUPERCEDES PATTERN** — "Replace X with Y" or "Switch to Y" (superceding X): extract TWO constraints:
> a. `subject_role_general=codebase_root`, `predicate=prohibits_dependency`, `object_role_general=X` — prohibition of old technology
> b. `subject_role_general=codebase_root`, `predicate=requires_dependency`, `object_role_general=Y` — requirement of new technology

This produces a paired prohibit+require for every technology-swap ADR, ensuring both sides of the replacement are represented as constraints.

### 2. Resolution: `suppress_outweighed_requires`

A new resolution pass runs after `suppress_outweighed_prohibits`:

```python
def suppress_outweighed_requires(
    violations: list[Violation],
    active_prohibits: list[ConstraintEdge],
) -> list[Violation]:
```

A `requires_*` violation is suppressed when a `prohibits_*` constraint on the same object has:

- **Higher specificity**, OR
- **Equal specificity but a newer ADR** (`adr_id` comparison)

This handles the "Replace Flask with Django" case: if ADR-010 says `prohibits_dependency(flask)` with higher specificity than a `requires_dependency(flask)` from an older ADR, the require violation is dropped.

### 3. Separate from central resolution (future)

`suppress_outweighed_requires` and `suppress_outweighed_prohibits` are narrow, symmetric passes that handle specificity-weight conflicts between opposite-polarity constraints on the same object. They are not the full resolution system. A central resolution tier (planned for later) will handle more complex interactions (e.g., transitive conflicts, multi-constraint overrides). These two passes are kept as separate, composable filters for now.

## Consequences

- Technology-swap ADRs now produce complete constraint pairs (prohibit old + require new).
- Spurious `requires_dependency` violations for the old technology are suppressed when a higher-specificity prohibit covers the same object.
- Both resolution passes are O(V × P) where V = violations and P = active constraints of the opposite polarity. Acceptable for typical violation counts; optimization deferred.
- The extraction prompt renumbered rules (rule 3 → rule 4 for LAYER DISAMBIGUATION).
- `derive_package_context` filters out single-component FQNs (e.g., bare `app`) from the package context list, since the open-graph ADG (ADR 009 section 8) now creates synthetic root nodes that would otherwise appear as valid `role_general` targets.