from dataclasses import dataclass
from enum import Enum
from services.fqn import FQN
from services.models import FQNNode, ConstraintEdge

class MatchStatus(Enum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    NO_MATCH = "no_match"

@dataclass
class MatchResult:
    status: MatchStatus
    matched_fqns: list[FQN]

def compute_specificity(edge: ConstraintEdge, match_status: MatchStatus) -> float:
    if match_status == MatchStatus.NO_MATCH:
        return 0.0
    
    depth = len(edge.subject.rstrip(".").split("."))
    if match_status == MatchStatus.EXACT:
        return float(depth) + 1.0
    return float(depth)

def match_fqn(pattern: str, nodes: list[FQNNode]) -> MatchResult:
    matches: list[tuple[FQN, MatchStatus]] = []
    for node in nodes:
        status = fqn_matches_pattern(node.fqn, pattern)
        if status != MatchStatus.NO_MATCH:
            matches.append((node.fqn, status))
    
    if not matches:
        return MatchResult(status=MatchStatus.NO_MATCH, matched_fqns=[])
    
    best = max(matches, key=lambda x: _status_priority(x[1]))[1]
    matched_fqns = sorted(
        [fqn for fqn, s in matches if s == best], key =str
    )
    return MatchResult(status=best, matched_fqns=matched_fqns)

def _status_priority(status: MatchStatus)-> int:
    return {
        MatchStatus.EXACT: 2, 
        MatchStatus.WILDCARD: 1, 
    }.get(status, 0)

def fqn_matches_pattern(fqn: FQN, pattern: str) -> MatchStatus:
    if str(fqn) == pattern:
        return MatchStatus.EXACT

    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        if str(fqn).startswith(prefix + "."):
            return MatchStatus.WILDCARD

    return MatchStatus.NO_MATCH



    