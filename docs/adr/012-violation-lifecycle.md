# 12. Violation Lifecycle: Dismissals-Only Persistence

Date: 2026-07-09

## Status

Accepted

## Context

CPT detection produces violations on every run, but detected violations are ephemeral: they vanish when the process exits. Issue #42 requires a way to dismiss false positives so they don't reappear on subsequent runs. The original issue proposed a two-status model (open/dismissed) with full persistence, but storing open violations is redundant when CPT recomputes them fresh each run.

The design must also integrate with the incremental ADG update (issue #10), where constraint edges and structural nodes change between commits.

## Decision

### 1. Store dismissals only, not open violations

CPT is the single source of truth for "what violations exist right now." Dismissals are a filter layer: `cpt violation list` runs detect, subtracts dismissals, and shows the remainder. There is no "open" status in storage.

### 2. Dismissal identity key: (subject, predicate, object, matched_fqn, adr_id)

The 5-tuple identifies a dismissal. Including `adr_id` enables cleanup when an ADR is superseded: deleting constraints for that `adr_id` also deletes dismissals for that `adr_id`. The original 4-tuple (subject, predicate, object, matched_fqn) is the violation identity; `adr_id` is added for dismissals to scope them to the originating ADR.

### 3. Dismissal IDs are 5-char hex hashes

Users dismiss violations by a short deterministic hash of the 5-tuple, e.g. `cpt violation dismiss a3f2c`. Collision probability is negligible at research scale. `ponytail: 5-char hex hash, upgrade to longer if collisions ever observed`.

### 4. Cleanup rules

- **Seed rebuild**: wipe all dismissals. Reproducibility over persistence.
- **ADR superseded**: when constraint edges for an `adr_id` are deleted, delete dismissals for that `adr_id`.
- **Code changes**: dismissals are NOT cleared. Trust the human; they can re-evaluate manually.
- **No longer detected**: dismissals for identity keys not in current detection results are kept. They are only removed by seed rebuild or ADR deletion.

### 5. Neo4j schema: flat Dismissal node

Dismissals are stored as standalone nodes, not as relationships to FQNNode or ConstraintEdge. This decouples them from structural graph mutations during ADG updates.

```
(:Dismissal {
  short_id: "a3f2c",
  identity_hash: "<SHA-256 of 5-tuple>",
  subject: "app.auth",
  predicate: "prohibits_dependency",
  object: "app.external.*",
  matched_fqn: "app.external.stripe",
  adr_id: "ADR-003",
  dismissed_at: "2026-07-09T..."
})
```

### 6. CLI surface

- `cpt violation list --repo <id>`: runs detect, subtracts dismissals, shows violations with short IDs.
- `cpt violation dismiss <short_id> --repo <id>`: persists a dismissal by short_id.

## Consequences

- Single source of truth: CPT output, not a stored violation table.
- Dismissals are permanent until explicitly cleaned up by seed rebuild or ADR deletion.
- A dismissed false positive could mask a real violation after code changes. Accepted trade-off: simplicity over automatic invalidation.
- No cross-store coordination needed; dismissals and ADG live in the same Neo4j database.