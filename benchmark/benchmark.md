# ADRLinter Research Benchmark Proposal

> Phase 3 deliverable. Core pipeline (Phase 1) and optimization (Phase 2) precede this work.

## Thesis Claims and Evaluation Mapping

| Thesis Claim | Domain Instantiation | Evaluation | Baseline |
|---|---|---|---|
| Graph-based memory representation | ADG encodes code structure + ADR constraints | Demonstrated indirectly via subtask 2 results | N/A (representation claim is implicit) |
| Relation-aware memory retrieval | CPT traverses ADG to find violations | Precision, relative coverage, violation type breakdown | Pi+LLM (2-3 models, tool access) |
| Selective forgetting and memory optimization | R1-R4 resolution dismisses conflicting violations | Precision before/after resolution (ablation) | No resolution (raw CPT output) |

## Test Instance Definition

A test instance is a (repo, commit, ADR set) triple, evaluated at two levels:

1. **Retrieval level (subtask 2)**: Does CPT find violations that an LLM baseline misses, particularly transitive violations?
2. **Resolution level (subtask 3)**: Does R1-R4 resolution improve precision by dismissing conflicting violations?

Subtask 1 (representation) is not evaluated independently. The graph representation claim is demonstrated indirectly: if CPT finds violations through transitive paths that an LLM cannot reach, the graph structure carries information that flat representation does not.

## Subtask 2: CPT Detection vs Pi+LLM

### Setup

- **System under test**: ADRLinter CPT engine
- **Baseline**: Pi agent harness (github.com/earendil-works/pi) with SOTA models
  - 2-3 models: 1 frontier (Claude/GPT-4, smaller subset due to cost), 2 open/affordable (DeepSeek, GLM)
  - Pi provides tool access: grep and file reading, same tools a developer would use
  - Temperature 0, 3 runs per instance, 20 tool call cap
  - Same ADR text and repo file tree provided to both CPT and Pi
  - Prompt: "Find all violations of these architectural decisions in this codebase." No hints about graph traversal or CPT.

### Metrics

| Metric | Definition | Notes |
|---|---|---|
| Precision | confirmed violations / total violations reported | From human dismissal review |
| Relative coverage | violations found by CPT but not LLM, and vice versa | No absolute recall (no oracle) |
| Violation type breakdown | direct vs transitive violations per method | Headline result: CPT should dominate on transitive violations |

### Ground Truth

Human review via the existing dismissal mechanism in `app/services/cpt/dismissal.py`:

- Confirmed violation = true positive
- Dismissed violation = false positive
- No separate annotation step needed

Relative recall is approximated by cross-referencing: violations CPT finds that Pi misses, and vice versa.

## Subtask 3: R1-R4 Resolution Ablation

### Setup

- **System under test**: ADRLinter CPT + R1-R4 resolution
- **Baseline**: Same CPT output without R1-R4 resolution (raw violations)
- **Method**: Run pipeline twice, with and without resolution. Compare precision.

### Metrics

| Metric | Definition | Notes |
|---|---|---|
| Precision before resolution | confirmed / (confirmed + dismissed) in raw CPT output | Includes unresolved conflicts |
| Precision after resolution | confirmed / (confirmed + dismissed) in post-R1-R4 output | Conflicts resolved |
| Recall delta | violations correctly dismissed / violations incorrectly dismissed | Did resolution remove true violations? |

### Conflict Instances

Natural ADR-ADR conflicts are rare (~0.29% of pairs per Dhaouadi et al. 2025). Strategy:

1. **First**: Curate naturally occurring conflicts from real repos
2. **If fewer than ~10 natural conflicts across all repos**: inject synthetic conflict ADRs into real repos. Only the conflicting ADR text is synthetic; the code and structural context remain real.

## Dataset

### Inclusion Criteria

A repo qualifies if:

1. **3+ ADRs** with identifiable PROHIBITS/REQUIRES constraints (not just "we decided X")
2. **Python codebase** (ADRLinter currently supports Python only)
3. **Code-inferable constraints** (not deployment or organizational decisions)
4. **At least one ADR is violated by the code** (otherwise no violations to detect)
5. **Publicly available and archived** (for reproducibility)

### Candidate Repos

| Repo | ADRs | Notes |
|---|---|---|
| python-tuf | 10 | Confirmed. Nygard format, checkable constraints (OOP, style, serialization). 1,712 stars. |
| (3-4 more from ADR-Study-Dataset) | TBD | Filter for Python + 3+ code-inferable ADRs |

### Source Datasets

- **ADR-Study-Dataset** (Buchgeher et al. 2023): 921 repos, 6,362 ADRs. Primary source for candidate repos.
- **Su et al. 2026 replication package**: 109 repos, 980 ADRs. Pre-filtered for quality.

### Size Target

5 repos with 3+ ADRs each. Estimated 25-50 violation instances for subtask 2. Conflict instances depend on natural occurrence; inject if fewer than ~10.

## Phasing

| Phase | Deliverable | Benchmark Status |
|---|---|---|
| Phase 1 | Core pipeline (ADG + CPT + R1-R4 + dismissal) | Benchmark proposal written (this document) |
| Phase 2 | Pipeline with optimization (MDS, incremental) | Benchmark implementation begins |
| Phase 3 | Benchmarking and research writing | Full evaluation execution |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Natural ADR-ADR conflicts too rare for subtask 3 | High | Inject synthetic conflict ADRs into real repos. Only ADR text is synthetic. |
| Pi+LLM outperforms CPT on some instances | Medium | Report honestly. Highlight transitive violation advantage. |
| Single annotator bias | High | Acknowledge in paper. Consider second annotator for subset if feasible. |
| Frontier model costs exceed budget | Medium | Run frontier on 2-3 repos, open models on all 5. |
| Repo selection bias toward well-maintained projects | Medium | Acknowledge in limitations. Well-maintained projects are the realistic target. |

## Assumptions

| Assumption | If Wrong |
|---|---|
| CPT engine can process real-world repos at benchmark scale | Verify pipeline runs on python-tuf before committing to dataset. |
| Pi framework supports tool access pattern (grep, file reading, step budget) | Verify Pi's tool API supports the proposed protocol. |
| 5 repos with 3+ ADRs provide enough instances for statistical meaning | Pilot on python-tuf first. Expand if too few violations. |
| Dismissal mechanism suffices for ground truth annotation | May need lightweight annotation UI if manual JSON editing is too slow. |
| ADR-Study-Dataset contains enough Python repos with code-inferable ADRs | Supplement with manual GitHub search if needed. |

## Related Work

| Work | Relevance |
|---|---|
| Su et al. (ICSA 2026) | LLM-based ADR violation detection. 109 repos, 980 ADRs. Strong on explicit violations, weak on implicit. Closest existing work. |
| ArchBench (ICSA 2026) | Architecture benchmark for generation tasks (ADR writing, service gen). Not violation detection. |
| SWE-bench | Evaluation methodology inspiration: real repo, real issue, real test. Not architecture-specific. |
| Buchgeher et al. (2023) | ADR-Study-Dataset. 921 repos, 6,362 ADRs. Primary source for candidate repos. |
| Dhaouadi et al. (FSE 2025) | Measured contradictory decision pairs in commit messages. 0.29% rate. Evidence that natural conflicts are rare. |
| ADRMiner (ECSA 2026) | 547 repos, 4,316 ADRs. Text mining and classification. Potential source for additional candidates. |

## Open Items

- [ ] Select 4 additional repos from ADR-Study-Dataset (python-tuf confirmed)
- [ ] Design Pi+LLM prompt template (fair, no CPT hints)
- [ ] Verify Pi tool access protocol (grep, file reading, step budget)
- [ ] Pilot ADRLinter pipeline on python-tuf
- [ ] Estimate annotation workload from pilot results
- [ ] Decide whether second annotator is needed