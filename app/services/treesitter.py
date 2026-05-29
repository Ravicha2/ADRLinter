"""Track A: parse Python repositories into Architectural Decision Graph nodes and edges."""

from __future__ import annotations

from pathlib import Path

from services.models import ADG


def parse_repo(repo_path: Path) -> ADG:
    """Walk all .py files in repo_path and extract FQN nodes and edges.

    Stub: returns empty ADG. Implementation will be driven by tests in
    tests/services/test_treesitter.py.
    """
    return ADG()