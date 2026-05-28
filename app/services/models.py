from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FQNNode:
    fqn: str
    kind: str  # "module" | "class" | "function" | "method"
    file_path: str
    line_start: int
    line_end: int


@dataclass
class Edge:
    source: str
    target: str
    kind: str  # "CALLS" | "INHERITS" | "CONTAINS" | "IMPORTS"


@dataclass
class ADG:
    nodes: list[FQNNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


@dataclass
class MDSResult:
    hubs: list[str] = field(default_factory=list)
    dominance_counts: dict[str, int] = field(default_factory=dict)