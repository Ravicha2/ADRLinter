from dataclasses import dataclass
from enum import Enum
from services.fqn import FQN
from collections import Counter
from services.models import FQNNode, ConstraintEdge

SEGMENT_THRESHOLD = 0.9

class MatchStatus(Enum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    SEGMENT = "segment"
    NO_MATCH = "no_match"

@dataclass
class MatchResult:
    status: MatchStatus
    matched_fqns: list[FQN]

def compute_specificity(edge: ConstraintEdge, match_status: MatchStatus, jaccard_score: float=0.0) -> float:
    if match_status == MatchStatus.NO_MATCH:
        return 0.0
    
    depth = len(edge.subject.rstrip(".").split("."))
    if match_status == MatchStatus.EXACT:
        return float(depth) + 1.0
    if match_status == MatchStatus.SEGMENT:
        return float(depth) + jaccard_score
    return float(depth) # WILDCARD

def _multiset_jaccard(a: Counter, b: Counter) -> float:
    intersection = sum(min(a[k], b[k]) for k in a.keys() & b.keys())
    union = sum(max(a[k],b.get(k,0)) for k in a.keys() | b.keys())
    return intersection / union if union else 0.0

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
        MatchStatus.EXACT: 3, 
        MatchStatus.WILDCARD: 2, 
        MatchStatus.SEGMENT: 1
    }.get(status, 0)

def fqn_matches_pattern(fqn: FQN, pattern: str) -> MatchStatus:
    # Exact Match
    if str(fqn) == pattern:
        return MatchStatus.EXACT
    
    # Wildcard Match
    if pattern.endswith(".*"):
        prefix = pattern[:-2] # strip .*

        if str(fqn).startswith(prefix + "."):
            return MatchStatus.WILDCARD
        
        prefix_parts = prefix.split(".")
        fqn_parts = fqn.parts
        if len(fqn_parts) > len(prefix_parts): # fqn is a child
            fqn_prefix_counter = Counter(fqn_parts[:len(prefix_parts)])
            pat_prefix_counter = Counter(prefix_parts)
            if _multiset_jaccard(fqn_prefix_counter, pat_prefix_counter) >= SEGMENT_THRESHOLD:
                return MatchStatus.SEGMENT
    else:
        # Segment Match
        fqn_counter = Counter(fqn.parts)
        pat_counter = Counter(pattern.split("."))
        if _multiset_jaccard(fqn_counter, pat_counter) >= SEGMENT_THRESHOLD:
            return MatchStatus.SEGMENT

    return MatchStatus.NO_MATCH



    