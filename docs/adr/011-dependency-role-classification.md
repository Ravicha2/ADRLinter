# 11. Dependency Role Classification for False-Positive Suppression

Date: 2026-07-09

## Status

Accepted

## Context

ADRLinter builds an Architectural Dependency Graph (ADG) where all external packages appear as `EXTERNAL` FQNKind nodes with no further distinction. When `_reachable_nodes` performs BFS traversal to check architectural constraints, it treats edges through dev-tool packages (pytest, black, mypy) identically to edges through application packages (flask, django). This produces false positives: violations that flag dev-tool dependencies as architecturally meaningful when they are not.

Example: a `PROHIBITS_DEPENDENCY` rule flags "module X depends on pytest" even though pytest is a dev tool that has no architectural significance.

Two peer-reviewed studies validate this approach:

- **Latendresse et al. (ASE'22)** "Not All Dependencies are Equal": 59% of runtime dependencies are never shipped to production. Well-known dev tools (pytest, eslint, babel-cli) are unambiguous: they are never used in production across all studied projects. Package functionality alone does not determine production status; context matters, but for known dev tools the classification is a reliable lookup.
- **Weeraddana et al. (FSE'24)** "Dependency-Induced Waste in CI": 92.63% of CI waste comes from unused development dependencies. The paper recommends focusing on development dependencies first because they "are unlikely to affect production environments."

The lit review on this topic (`lit-review-conflict-res/index.md`) identifies a three-part solution: (1) dependency role classification, (2) semantic overlay on topological base, (3) noise suppression. This ADR implements the first part as the highest-impact, lowest-cost fix.

## Decision

### 1. Add `DependencyRole` enum and `role` field to `FQNNode`

```python
class DependencyRole(Enum):
    INTERNAL = "internal"          # project modules (default for non-EXTERNAL nodes)
    DEV_TOOL = "dev_tool"          # pytest, black, mypy, etc.
    INFRASTRUCTURE = "infrastructure"  # redis, elasticsearch, celery
    APPLICATION = "application"    # flask, django, requests
    UNKNOWN = "unknown"            # unclassified external package
```

`FQNNode` gets a `role` field defaulting to `INTERNAL`. `EXTERNAL` nodes receive a role based on classification.

### 2. Classify `EXTERNAL` nodes using a hardcoded Python dev-tool registry

A set `PYTHON_DEV_TOOLS` contains well-known Python dev/test/lint packages. When `add_external_nodes` creates `EXTERNAL` nodes, it checks the root package name (first component of the FQN) against this registry. Matching nodes get `role=DEV_TOOL`.

This registry covers the highest-impact false positives. Well-known dev tools are unambiguous across all projects (Latendresse et al., Finding 5). Project-specific classification via `pyproject.toml` extras is a separate enhancement (ADR 012, future).

### 3. Skip `DEV_TOOL` nodes in reachability traversal

In `engine.py`, `_reachable_nodes` filters out `DEV_TOOL`-role nodes. A node with `role=DEV_TOOL` is excluded from the BFS adjacency, so no reachability path can traverse through it. This eliminates false violations for dev-tool dependencies.

Implementation: pass a `skip_roles` parameter to `_reachable_nodes` and filter edges whose target is a `DEV_TOOL` node.

### 4. What this does NOT do

- Does not filter `INFRASTRUCTURE` or `APPLICATION` nodes: these are architecturally meaningful and should remain in the graph. Flask in the data layer IS a real violation if the ADR prohibits it.
- Does not read project config files yet: that is a separate enhancement.
- Does not change `Edge` model: the role is on the node, not the edge.

## Consequences

- Dev-tool false positives (pytest, black, mypy, etc.) are eliminated from violation output.
- `INFRASTRUCTURE` and `APPLICATION` nodes remain in the graph and can still trigger violations, which is correct: flask in a data layer violating an ADR is a real architectural concern.
- The hardcoded registry must be maintained as new dev tools are identified. The cost is low (a set of ~30 package names) and the benefit is high (covers the most common false positives).
- `UNKNOWN` external nodes (packages not in the registry) remain in the graph and can still trigger violations. This is conservative: it may produce false positives for obscure dev tools not yet in the registry, but it avoids false negatives from misclassifying application packages.