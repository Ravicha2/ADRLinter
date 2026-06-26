# 8. Symbolic Constraint Resolution

Date: 2026-06-26

## Status

Accepted (supersedes ADR 7)

## Context

ADR 7 introduced an LLM resolution layer that remaps orphan FQN patterns after extraction. This revealed two deeper problems:

1. **Naming**: The LLM guesses FQN strings (e.g., `authentication_logic`) that don't correspond to real code entities. No reliable way to bridge "auth" vs "authenticate" vs `app.auth`.
2. **Hierarchy**: The LLM guesses the depth/level of the target entity (module vs class vs method).

Prompting the LLM to produce correct FQN patterns is unreliable because natural language doesn't map 1:1 to code structure. The LLM invents names and depths that don't exist.

## Decisions

### 1. SymbolicConstraint as intermediate representation

Replace direct LLM→FQN extraction with a two-stage pipeline:

```
ADR text → langextract → SymbolicConstraint → ADG traversal + substring matching → ResolvedConstraint → ConstraintEdge → merge
```

`SymbolicConstraint` decouples the ADR's natural language concepts from code structure:

```python
class SymbolicConstraint:
    subject_role_general: str    # real module name from prompt (e.g., "app.api")
    subject_role_specific: str  # ADR concept (e.g., "endpoint")
    predicate: PredicateType
    object_role_general: str     # real module name from prompt (e.g., "app.db")
    object_role_specific: str   # ADR concept (e.g., "connector")
    justification: str
    extraction_text: str         # verbatim from ADR for traceability
    adr_id: str                  # parsed from ADR metadata
    adr_path: str                # parsed from ADR metadata
```

### 2. LLM picks from module list, not free-form

The extraction prompt provides the list of top-level ADG modules (e.g., `app.auth`, `app.db`, `app.services`). The LLM picks `role_general` values from this list instead of inventing FQN patterns. `role_specific` values come from the ADR text.

LLM extracts 7 fields: `subject_role_general`, `subject_role_specific`, `predicate`, `object_role_general`, `object_role_specific`, `justification`, `extraction_text`. `adr_id` and `adr_path` are parsed separately.

### 3. Kind-filtered resolution

Both subject and object are narrowed by kind before matching:

```python
SUBJECT_KINDS = {
    "requires_dependency":      {"module"},
    "prohibits_dependency":     {"module"},
    "requires_implementation":  {"module", "class"},
    "prohibits_implementation": {"module", "class"},
}

OBJECT_KINDS = {
    "requires_dependency":      {"module"},
    "prohibits_dependency":    {"module"},
    "requires_implementation":  {"class", "function", "method"},
    "prohibits_implementation": {"class", "function", "method"},
}
```

### 4. Resolution algorithm

For each side (subject, object):

1. **Kind filter**: narrow ADG nodes by `SUBJECT_KINDS` or `OBJECT_KINDS` for the predicate
2. **General match**: exact/wildcard match `role_general` against ADG module nodes
3. **Walk CONTAINS**: collect children of matched modules, filter by kind
4. **Specific narrow**: substring-match `role_specific` against children's short names
5. **Fallback**: if step 2 finds nothing, substring-match `role_specific` against module segments
6. **No match**: log and skip (flag for human review in neo4j)

Substring matching priority: exact > prefix overlap > substring containment. No embeddings, no thresholds, no tiers.

### 5. Wildcards are implied, not explicit

No wildcards in `role_general` fields. Wildcard patterns in the resolved `ConstraintEdge` are implied by kind filter + CONTAINS walk. `subject_role_general: "app"` with `SUBJECT_KINDS: {"module"}` resolves to `app.*` after walking all module children.

### 6. External dependencies bypass symbolic resolution

If `object_role_general` matches no ADG module AND the predicate is a dependency type (`requires_dependency`, `prohibits_dependency`), create an EXTERNAL node and wildcard constraint directly. External dependencies have no internal structure to resolve.

### 7. ResolvedConstraint tracks match source

```python
class ResolvedConstraint:
    constraint_edge: ConstraintEdge
    subject_matched_by: str  # "specific" | "general_wildcard" | "fallback" | "human"
    object_matched_by: str   # same
```

### 8. Specificity computed during conflict resolution

`ConstraintEdge.specificity` is no longer computed during merge. It is computed during the conflict resolution phase instead.

### 9. NameResolver coexists

`NameResolver` (suffix index, pattern matching) stays for ADG construction (resolving imports, calls, inheritance). The symbolic resolver is a separate module for constraint resolution only.

### 10. Flag human = log and skip

Unresolved constraints are logged and skipped. Manual fixes applied directly in neo4j.

### 11. `char_interval` removed

`extraction_text` replaces `char_interval` for traceability. Verbatim ADR text is sufficient; character positions are no longer tracked.

### 12. `subject_role_specific` may be unused

For dependency predicates where `SUBJECT_KINDS` is `{"module"}`, there's nothing to narrow within the module. The field is kept for consistency; no need to make it Optional.

## Consequences

- Constraint extraction is more reliable: LLM picks from a bounded module list instead of inventing FQN strings
- Resolution is deterministic (no LLM calls during merge): substring matching against known ADG nodes
- False positives from broad matches are acceptable; conflict resolution filters them downstream
- `merge.py` is rewritten: `resolve_orphans` and `_call_resolution_llm` are replaced by the symbolic resolver
- Extraction prompt changes: 7 fields instead of 3, plus module list in prompt context
- ADR 7's LLM resolution layer (`resolve_orphans`, `_call_resolution_llm`, `gather_candidates`) is removed
- Segment matching removal from ADR 7 is preserved; matching remains EXACT/WILDCARD/NO_MATCH

## Superseded decisions from ADR 7

| ADR 7 Decision | New Status |
|----------------|------------|
| LLM remaps orphan FQN patterns | Replaced by symbolic resolution |
| Candidate collection via prefix-scoped walk | Replaced by ADG traversal + kind filter |
| One LLM call per orphaned side | Removed; resolution is deterministic |
| `gather_candidates` function | Removed |
| `_call_resolution_llm` | Removed |
| In-place constraint modification | Replaced; `SymbolicConstraint` produces new `ConstraintEdge` |