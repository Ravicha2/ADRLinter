"""ADG Pipeline: orchestrates constraint merge, specificity computation, augmentation, and detection.

Owns the sequencing gap where ConstraintEdge.specificity was never computed
between merge_constraints and detect. Also normalizes the mixed mutation
strategy (merge returns new, augment mutates in-place) so callers always
receive fresh ADG instances.

Usage (production):
    pipeline = ADGPipeline()
    result = pipeline.run(repo_path, adr_dir, config, commit=sha)

Usage (tests, pure data):
    inputs = PipelineInputs(adg=adg, constraints=constraints, diff_result=diff)
    result = pipeline.run_prepared(inputs)
    assert result.violations[0].constraint.specificity > 0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from services.adg.merge import merge_constraints
from services.cpt.dismissal import Dismissal, filter_dismissed
from services.cpt.diff_processor import augment_adg, process_diff
from services.cpt.engine import detect as cpt_detect
from services.models import ADG, Diff, ConstraintEdge, DiffResult, SymbolicConstraint
from services.resolver import MatchStatus


def pattern_specificity(pattern: str) -> float:
    """Compute specificity for a constraint pattern string.

    Wildcard patterns (ending .*) get depth only.
    Exact patterns get depth + 1.0.

    Strips the .* wildcard suffix before counting depth so
    'app.routes.*' has depth 2 (not 3).
    """
    is_wildcard = pattern.endswith(".*")
    clean = pattern[:-2] if is_wildcard else pattern
    depth = len(clean.rstrip(".").split("."))
    return float(depth) + (0.0 if is_wildcard else 1.0)


def adg_with_specificity(adg: ADG) -> ADG:
    """Return a NEW ADG where every ConstraintEdge has specificity set.

    This closes the gap between merge_constraints (which sets specificity=0.0)
    and the resolution engine (which compares specificity values).
    """
    new_edges: list[ConstraintEdge] = []
    for edge in adg.constraint_edges:
        new_edges.append(ConstraintEdge(
            subject=edge.subject,
            predicate=edge.predicate,
            object=edge.object,
            justification=edge.justification,
            adr_id=edge.adr_id,
            adr_path=edge.adr_path,
            specificity=pattern_specificity(edge.subject),
        ))
    return ADG(
        nodes=list(adg.nodes),
        edges=list(adg.edges),
        constraint_edges=new_edges,
    )


# ---------------------------------------------------------------------------
# Mutation normalization
# ---------------------------------------------------------------------------

def augment_immutable(adg: ADG, diff: Diff) -> ADG:
    """Wrap the in-place augment_adg so it returns a fresh ADG.

    Callers never see their input ADG mutated.
    """
    clone = ADG(
        nodes=list(adg.nodes),
        edges=list(adg.edges),
        constraint_edges=list(adg.constraint_edges),
    )
    augment_adg(clone, diff)
    return clone


# ---------------------------------------------------------------------------
# Pure-data test input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PipelineInputs:
    """Everything needed to run detection without touching git/filesystem/LLM."""
    adg: ADG
    constraints: list[SymbolicConstraint]
    diff_result: DiffResult
    diff: Diff | None = None
    project_root: Path | None = None


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class ADGPipeline:
    """Orchestrates the full ADG -> CPT detection pipeline."""

    def run_prepared(self, inputs: PipelineInputs) -> "CPTResult":
        """Pure pipeline: no io, no mutation surprises.

        Merge constraints, compute specificity, optionally augment, then detect.
        """
        from services.cpt.engine import CPTResult

        merged = merge_constraints(inputs.adg, inputs.constraints, project_root=inputs.project_root)
        merged = adg_with_specificity(merged)

        if inputs.diff is not None:
            merged = augment_immutable(merged, inputs.diff)

        return cpt_detect(inputs.diff_result, merged)

    def run_with_dismissals(self, inputs: PipelineInputs, dismissals: list[Dismissal]) -> "CPTResult":
        """Run detect pipeline, then filter out dismissed violations.

        Pure function: no io, dismissals passed in by caller.
        """
        from services.cpt.engine import CPTResult

        result = self.run_prepared(inputs)
        filtered = filter_dismissed(result.violations, dismissals)
        return CPTResult(
            violations=filtered,
            orphans=result.orphans,
            self_loop_constraints=result.self_loop_constraints,
        )

    @staticmethod
    def build_seed(adg: ADG, constraints: list[SymbolicConstraint], project_root: Path | None = None) -> ADG:
        """Merge constraints into ADG and compute specificity. No diff, no detection.

        For cli/main.py:seed_build().
        """
        merged = merge_constraints(adg, constraints, project_root=project_root)
        return adg_with_specificity(merged)