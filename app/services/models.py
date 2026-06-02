from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from services.fqn import FQN


class FQNKind(Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


@dataclass
class FQNNode:
    fqn: FQN
    kind: FQNKind
    file_path: str
    line_start: int
    line_end: int
    start_byte: int = 0
    end_byte: int = 0


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


# ---------------------------------------------------------------------------
# Diff Processor data models
# ---------------------------------------------------------------------------


@dataclass
class FileChange:
    path: str
    status: str  # "added" | "modified" | "deleted" | "renamed"
    old_path: str | None = None  # for renames


@dataclass
class CommitDiff:
    commit_sha: str
    parent_sha: str | None  # None for first commit
    changed_files: list[FileChange] = field(default_factory=list)
    file_contents: dict[str, bytes] = field(default_factory=dict)  # path -> content at commit SHA
    parent_contents: dict[str, bytes] = field(default_factory=dict)  # path -> content at parent SHA


@dataclass
class ChangedFQN:
    fqn: FQN
    change_type: str  # "added" | "modified" | "deleted"
    file_path: str
    enclosing_class: FQN | None = None
    enclosing_module: FQN | None = None


@dataclass
class DiffResult:
    commit_sha: str
    parent_sha: str | None = None
    changed_files: list[FileChange] = field(default_factory=list)  # for ADG Update
    changed_fqns: list[ChangedFQN] = field(default_factory=list)  # for CPT