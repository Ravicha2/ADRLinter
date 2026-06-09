# 4. Expand ADR Constraint Predicate Ontology and Improve Extraction Prompt

Date: 2026-06-09

## Status

Accepted

## Context

The ADR constraint extraction pipeline currently uses a 2-predicate ontology:
- `prohibits_dependency`: the subject must not depend on the object
- `requires_implementation`: the subject must implement the object

Integration test results show two problems:

1. **Predicate forcing.** ADRs expressing negative implementation rules (e.g., "No other module shall implement authentication logic") have no matching predicate. The LLM forces these into `prohibits_dependency`, producing incorrect extractions.

2. **Over-generalization.** The LLM extracts bare `*` or overly broad wildcard subjects, and invents FQN prefixes not present in source text. Root causes: no explicit boundary between dependency vs. implementation, no scoping rules for wildcards, and a fabricated FQN in the third few-shot example.

## Decision

1. **Expand to 4 predicates** forming a 2x2 matrix:

   |  | Dependency | Implementation |
   |---|---|---|
   | **Prohibits** | `prohibits_dependency` | `prohibits_implementation` |
   | **Requires** | `requires_dependency` | `requires_implementation` |

   Definitions (to be stated explicitly in the extraction prompt):
   - **Dependency**: the subject module's imports or calls are constrained
   - **Implementation**: the subject module's internal code (what it defines, not what it uses) is constrained

2. **Rewrite the extraction prompt** with a structured format:
   - Predicate definitions listed with boundary rules
   - Scoping rules: wildcards for namespace-level constraints, specific FQNs for single modules, never bare `*`
   - Objects must be specific FQNs, no wildcards

3. **Replace few-shot examples** to achieve 1-per-predicate balance:
   - Drop one of the two `prohibits_dependency` examples (keep the one with clean FQN grounding)
   - Add `requires_dependency` example
   - Add `prohibits_implementation` example
   - Add 1 negative example (ADR with no enforceable constraints)

4. **Skip "allows" predicates** for now. Permissive constraints ("X may use Y") don't map to violation detection and would expand the ontology to 3x2 without clear CPT value.

5. **Update judge evaluation prompt** to include all 4 predicates with definitions.

6. **Add 2 new integration test fixtures**, one per new predicate, as separate test classes.

## Consequences

- Extraction should produce more precise predicate assignments, reducing forcing errors.
- Over-generalization should decrease due to explicit scoping rules and the negative example.
- The ontology is collapsible: if a predicate is underpopulated in real ADRs, it can be merged back.
- The judge prompt must stay in sync with the extraction prompt's predicate list.
- Future "allows" predicates can be added without breaking existing constraints (enum is additive).