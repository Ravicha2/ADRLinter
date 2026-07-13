# 13. ADG Update Strategy: Full Structural Rebuild

Date: 2026-07-11

## Status

Accepted

## Context

When a commit arrives, the ADG in Neo4j becomes stale: structural nodes and edges no longer reflect the current code. Issue #10 requires a way to update the ADG without re-running the expensive LLM extraction pipeline on every commit.

Two strategies were considered:

1. **Per-file incremental**: Use `delete_nodes_by_file` to remove stale nodes for changed files only, then re-insert those files' nodes and edges. Preserves cross-file edges but requires tracking and updating incoming edges from unchanged files.

2. **Full structural rebuild**: Wipe all FQNNodes and structural edges, re-parse the entire repo, and re-insert. Preserve constraint edges (via EXTERNAL placeholder reconnection) and dismissals separately.

The incremental approach has a cross-file edge problem: `DETACH DELETE` removes incoming edges from unchanged files pointing to changed-file nodes, and those edges are not recreated unless the referencing files are also re-processed. Resolving this requires tracking incoming edge dependencies per FQN, adding complexity.

## Decision

Use full structural rebuild. On every commit update:

1. Load constraint edges and dismissals from Neo4j
2. Wipe all FQNNodes and structural edges (via `delete_structural_data()`, which preserves constraint edges internally)
3. Re-parse repo into fresh ADG (structural nodes + edges only)
4. Re-insert structural nodes and edges; constraint edges reconnect to real code nodes via MERGE (EXTERNAL placeholders upgrade automatically)
5. Re-run CPT detection
6. Subtract dismissals (post-detection filter per ADR 012)

`delete_structural_data()` preserves constraint edges by loading them before the wipe and re-inserting them afterward with EXTERNAL placeholder endpoints. ADR-derived constraint edges and dismissals are the only data preserved across updates. The structural graph is always a clean rebuild.

## Consequences

- No cross-file edge staleness: the structural graph is always consistent.
- Constraint edges survive automatically via `delete_structural_data()`, which saves them before the wipe and re-inserts them with EXTERNAL placeholder endpoints. When real code nodes appear, MERGE reconnects them.
- Dismissals survive because they are standalone Dismissal nodes, not relationships to FQNNodes.
- Re-parsing the entire repo is O(repo size) on every commit. Acceptable for research scale. Optimization (incremental structural edges, indexing) is deferred.
- `delete_nodes_by_file` remains available in GraphStore for other use cases but is not used by the update flow.

## Risks

- **Performance**: Full re-parse on every commit. For large repos this will be slow. Optimization (incremental structural updates, graph indexing) can be added when throughput matters.
- **EXTERNAL node accumulation**: Constraint endpoints that never match code FQNs create EXTERNAL nodes that persist indefinitely. At research scale this is fine. Cleanup can be added if EXTERNAL node count becomes problematic.