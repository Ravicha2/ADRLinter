"""Track A: parse Python repositories into Architectural Decision Graph nodes and edges."""

from __future__ import annotations

from pathlib import Path
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from models import ADG, Edge, FQNNode

PY_LANGUGAGE = Language(tspython.language())

def _file_path_to_module_fqn(rel_path:str) -> str:
    """convert relative path to module FQN"""
    module = rel_path.removesuffix(".py").replace("/",".").replace("\\",".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    return module

def parse_repo(repo_path: Path) -> ADG:
    """
    Walk all .py files in repo_path and extract FQN nodes and edges.
    """
    nodes:list[FQNNode] = []
    edges:list[Edge] = [] 

    py_files = sorted(repo_path.rglob("*.py"))

    module_fqns: dict[str, str] = {}
    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        fqn = _file_path_to_module_fqn(rel_path)
        if not fqn:
            continue
        source = py_file.read_text()
        line_count = source.count("\n") + (0 if source.endswith("\n") else 1) or 1
        nodes.append(FQNNode(
            fqn=fqn,
            kind="module",
            file_path=rel_path,
            line_start=0,
            line_end=line_count - 1,
        ))
        module_fqns[fqn] = rel_path

    return ADG(nodes=nodes, edges=edges)


if __name__ == "__main__":
    import os
    repo = Path("../../repos/flask")
    print(os.listdir(repo))
    print(parse_repo(repo))