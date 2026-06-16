from __future__ import annotations

from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ConstraintEdge
from services.matching import MatchStatus

@dataclass
class Violation:
    constraint: ConstraintEdge
    changed_fqn: FQN
    matched_fqn: FQN
    match_status: MatchStatus
    evidence: str
    change_type: str

@dataclass
class CPTResult:
    violations: list[Violation] = field(default_factory=list)
    orphans: list[ConstraintEdge] = field(default_factory=list)
    neighborhood: set[FQN] = field(default_factory=set)
    