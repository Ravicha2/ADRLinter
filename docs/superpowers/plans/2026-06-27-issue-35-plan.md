# Issue #35 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-pair BFS with per-source BFS (`_reachable_nodes`), update `check_structural_predicates` and `check_change_triggered_predicates`, fix `REQUIRES` semantics to emit exactly one violation per subject when zero objects are reachable, and update CPT tests.

**Architecture:** `_reachable_nodes` returns a set of all reachable FQN strings from a single starting FQN. `PROHIBITS` checks if object FQNs are in this set. `REQUIRES` checks if any matching object FQN is in this set, emitting a single violation with the constraint's object pattern in the evidence string if none are found.

**Tech Stack:** Python 3.13, pytest, uv

## Global Constraints

- Python 3.13 syntax and typing conventions (`from __future__ import annotations`, built-in generics).
- Preserve existing data models (`Violation`, `CPTResult`, `MatchedConstraint`).
- All tests must pass via `uv run pytest` (specifically CPT engine tests).

---

### Task 1: Replace `_reachable` with `_reachable_nodes` and update `check_structural_predicates`

**Files:**
- Modify: `app/services/cpt/engine.py:39-109`
- Modify: `app/tests/services/cpt/test_engine.py:220-298`

**Interfaces:**
- Consumes: `_build_adjacency`, `MatchedConstraint`, `Edge`, `Violation`
- Produces: `_reachable_nodes(start: str, adjacency: dict[str, list[Edge]], kinds: set[str]) -> set[str]`

- [ ] **Step 1: Write the failing test for `_reachable_nodes`**

Modify `app/tests/services/cpt/test_engine.py` to add `test_reachable_nodes` to `TestCheckStructuralPredicates`:

```python
    def test_reachable_nodes(self) -> None:
        from services.cpt.engine import _reachable_nodes, _build_adjacency
        from services.models import Edge

        adjacency = _build_adjacency({
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
            Edge(source="app.auth.middleware", target="app.models.user", kind="IMPORTS"),
        })
        reachable = _reachable_nodes("app.api.users", adjacency, {"IMPORTS"})
        assert reachable == {"app.auth.middleware", "app.models.user"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -k test_reachable_nodes -v`
Expected: FAIL with `ImportError: cannot import name '_reachable_nodes'`

- [ ] **Step 3: Write minimal implementation in `engine.py`**

Modify `app/services/cpt/engine.py` to replace `_reachable` with `_reachable_nodes` and update `check_structural_predicates`:

```python
def _reachable_nodes(start: str, adjacency: dict[str, list[Edge]], kinds: set[str]) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([start])

    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, ()):
            if edge.kind not in kinds:
                continue
            if edge.target not in visited:
                visited.add(edge.target)
                queue.append(edge.target)

    return visited
```

Update `check_structural_predicates` in `app/services/cpt/engine.py`:

```python
def check_structural_predicates(
    matched_constraints: dict[int, MatchedConstraint],
    adjacency: dict[str, list[Edge]],
) -> list[Violation]:
    """PROHIBITS_*: evaluate once per constraint, no changed_fqn needed."""
    violations: list[Violation] = []
    for matched_constraint in matched_constraints.values():
        pred = matched_constraint.constraint.predicate

        if pred not in (PredicateType.PROHIBITS_DEPENDENCY, PredicateType.PROHIBITS_IMPLEMENTATION):
            continue

        kinds = {"IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.PROHIBITS_DEPENDENCY else {"CONTAINS", "CALLS"}
        label = "has dependency path to" if pred == PredicateType.PROHIBITS_DEPENDENCY else "implements"

        for subject_fqn, subject_status in matched_constraint.subject_matches:
            subject_str = str(subject_fqn)
            reachable = _reachable_nodes(subject_str, adjacency, kinds)
            for object_fqn, object_status in matched_constraint.object_matches:
                higher = subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status
                object_str = str(object_fqn)
                if object_str in reachable:
                    violations.append(Violation(
                        constraint=matched_constraint.constraint,
                        changed_fqn=subject_fqn, 
                        matched_fqn=subject_fqn,
                        match_status=higher,
                        evidence=f"{subject_str} {label} {object_str}",
                        change_type="structural",
                    ))
    return violations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -k TestCheckStructuralPredicates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/cpt/engine.py app/tests/services/cpt/test_engine.py
git commit -m "feat: replace _reachable with per-source _reachable_nodes in check_structural_predicates"
```

---

### Task 2: Update `check_change_triggered_predicates` for `REQUIRES` semantics and evidence format

**Files:**
- Modify: `app/services/cpt/engine.py:112-151`
- Modify: `app/tests/services/cpt/test_engine.py:305-434`

**Interfaces:**
- Consumes: `_reachable_nodes`, `MatchedConstraint`, `ChangedFQN`, `Violation`
- Produces: Updated `check_change_triggered_predicates`

- [ ] **Step 1: Write the failing tests for `REQUIRES` semantics and evidence**

Modify `app/tests/services/cpt/test_engine.py` in `TestCheckChangeTriggeredPredicates` to assert the new evidence string format and add `test_requires_wildcard_multiple_objects_semantics`:

```python
    def test_requires_dependency_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        adjacency = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.orders"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.api.orders")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 1
        assert violations[0].constraint.adr_id == "ADR-004"
        assert violations[0].evidence == "app.api.orders has no dependency on any module matching app.auth.middleware"

    def test_requires_implementation_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        adjacency = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.middleware"), MatchStatus.EXACT)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.middleware")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 1
        assert violations[0].evidence == "app.middleware does not implement any module matching app.auth.middleware"

    def test_requires_wildcard_multiple_objects_semantics(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.*",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        # Case 1: Zero objects reachable -> exactly 1 violation emitted
        adjacency_empty = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.orders"), MatchStatus.WILDCARD)],
                object_matches=[
                    (FQN.from_dotted("app.auth.a"), MatchStatus.WILDCARD),
                    (FQN.from_dotted("app.auth.b"), MatchStatus.WILDCARD),
                ],
            ),
        }
        changed = [_changed_fqn("app.api.orders")]
        violations_empty = check_change_triggered_predicates(matched, adjacency_empty, changed)
        assert len(violations_empty) == 1
        assert violations_empty[0].evidence == "app.api.orders has no dependency on any module matching app.auth.*"

        # Case 2: One object reachable -> 0 violations emitted
        adjacency_partial = _build_adjacency({
            Edge(source="app.api.orders", target="app.auth.a", kind="IMPORTS"),
        })
        violations_partial = check_change_triggered_predicates(matched, adjacency_partial, changed)
        assert len(violations_partial) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -k TestCheckChangeTriggeredPredicates -v`
Expected: FAIL due to old evidence format and old `_reachable` call / semantics.

- [ ] **Step 3: Write minimal implementation in `engine.py`**

Modify `app/services/cpt/engine.py` in `check_change_triggered_predicates`:

```python
def check_change_triggered_predicates(
    matched_constraints: dict[int, MatchedConstraint],
    adjacency: dict[str, list[Edge]],
    changed_fqns: list[ChangedFQN],
) -> list[Violation]:
    """REQUIRES_*: evaluate per changed_fqn, pre-filtered by subject_matches."""
    violations: list[Violation] = []
    for changed in changed_fqns:
        changed_str = str(changed.fqn)
        for matched_constraint in matched_constraints.values():
            pred = matched_constraint.constraint.predicate
            if pred not in (PredicateType.REQUIRES_DEPENDENCY, PredicateType.REQUIRES_IMPLEMENTATION):
                continue

            relevant_subjects = [
                (subject_fqn, subject_status) for subject_fqn, subject_status in matched_constraint.subject_matches
                if subject_fqn == changed.fqn or changed_str.startswith(str(subject_fqn) + ".")
            ]

            if not relevant_subjects:
                continue

            kinds = {"IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.REQUIRES_DEPENDENCY else {"CONTAINS", "CALLS"}
            label = "has no dependency on" if pred == PredicateType.REQUIRES_DEPENDENCY else "does not implement"

            for subject_fqn, subject_status in relevant_subjects:
                subject_str = str(subject_fqn)
                reachable = _reachable_nodes(subject_str, adjacency, kinds)
                if not any(str(object_fqn) in reachable for object_fqn, _ in matched_constraint.object_matches):
                    highest_status = subject_status
                    for _, object_status in matched_constraint.object_matches:
                        if _PRIORITY[object_status] > _PRIORITY[highest_status]:
                            highest_status = object_status

                    violations.append(Violation(
                        constraint=matched_constraint.constraint,
                        changed_fqn=changed.fqn,
                        matched_fqn=subject_fqn,
                        match_status=highest_status,
                        evidence=f"{subject_str} {label} any module matching {matched_constraint.constraint.object}",
                        change_type=changed.change_type,
                    ))
    return violations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -k TestCheckChangeTriggeredPredicates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/cpt/engine.py app/tests/services/cpt/test_engine.py
git commit -m "feat: implement per-source BFS and correct REQUIRES semantics/evidence in check_change_triggered_predicates"
```

---

### Task 3: Fix `test_detect_specificity_resolution` baseline test failure

**Files:**
- Modify: `app/tests/services/cpt/test_engine.py:658-696`

**Interfaces:**
- Consumes: `detect`, `ADG`, `DiffResult`
- Produces: Updated test assertion

- [ ] **Step 1: Write the updated test assertion**

Modify `app/tests/services/cpt/test_engine.py` in `TestDetect.test_detect_specificity_resolution`:

```python
    def test_detect_specificity_resolution(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        constraints = [
            ConstraintEdge(
                subject="app.*",
                predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Only middleware may implement auth.",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
                specificity=1.0,
            ),
            ConstraintEdge(
                subject="app.middleware",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Middleware must implement auth.",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
                specificity=2.0,
            ),
        ]
        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=constraints,
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/middleware/auth.py", status="modified")],
            changed_fqns=[_changed_fqn("app.middleware.auth")],
        )
        result = detect(diff, adg)
        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_IMPLEMENTATION]
        require_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.REQUIRES_IMPLEMENTATION]
        # app.middleware has its prohibit suppressed by requires, while app.auth retains its valid structural prohibit under full-ADG evaluation
        middleware_prohibits = [v for v in prohibit_violations if str(v.matched_fqn) == "app.middleware"]
        assert len(middleware_prohibits) == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -k test_detect_specificity_resolution -v`
Expected: PASS

- [ ] **Step 3: Run full CPT test suite to verify everything passes**

Run: `uv run pytest app/tests/services/cpt/test_engine.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/tests/services/cpt/test_engine.py
git commit -m "test: update test_detect_specificity_resolution assertion for full-ADG evaluation"
```
