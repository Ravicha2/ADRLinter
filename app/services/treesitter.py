"""Track A: parse Python repositories into Architectural Decision Graph nodes and edges."""

from __future__ import annotations

from pathlib import Path
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from models import ADG, Edge, FQNNode

PY_LANGUGAGE = Language(tspython.language())

def file_path_to_module_fqn(rel_path:str) -> str:
    """convert relative path to module FQN"""
    module = rel_path.removesuffix(".py").replace("/",".").replace("\\",".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    return module

def parse_file(py_file: Path, module_fqn:str, rel_path:str, source:bytes) -> tuple[list[FQNNode], list[Edge]]:
    """parse a single Python file and return FQN nodes and CONTAINS edges."""
    parser = Parser(PY_LANGUGAGE)
    tree = parser.parse(source)
    root = tree.root_node

    nodes: list[FQNNode] = []
    edges: list[Edge] = []

    def walk_tree(node, parent_fqn:str, parent_kind:str):
        """Recursively walk AST to extract class, function, method definitions"""
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            class_name = name_node.text.decode("utf-8")
            class_fqn = f"{parent_fqn}.{class_name}"
            nodes.append(FQNNode(
                fqn=class_fqn,
                kind="class",
                file_path=rel_path,
                line_start=node.start_point[0],
                line_end=node.end_point[0],
            ))
            edges.append(Edge(source=parent_fqn, target=class_fqn, kind="CONTAINS"))

            for child in node.children:
                walk_tree(child, class_fqn, "class")

        elif node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            func_name = name_node.text.decode("utf-8")
            func_fqn = f"{parent_fqn}.{func_name}"
            kind = "method" if parent_kind == "class" else "function"
            nodes.append(FQNNode(
                fqn=func_fqn,
                kind=kind,
                file_path=rel_path,
                line_start=node.start_point[0],
                line_end=node.end_point[0]
            ))
            edges.append(Edge(source=parent_fqn, target=func_fqn, kind="CONTAIN"))
            for child in node.children:
                walk_tree(child, func_fqn, kind)
        
        elif node.type == "decorated_definition":
            for child in node.children:
                walk_tree(child, parent_fqn, parent_kind)
        
        else:
            for child in node.children:
                walk_tree(child, parent_fqn, parent_kind)

    for child in root.children:
        walk_tree(child, module_fqn, "module")

    return nodes, edges

def parse_repo(repo_path: Path) -> ADG:
    """
    Walk all .py files in repo_path and extract FQN nodes and edges.
    """
    nodes:list[FQNNode] = []
    edges:list[Edge] = [] 

    py_files = sorted(repo_path.rglob("*.py"))

    # create module nodes from file paths
    module_fqns: dict[str, str] = {}
    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        fqn = file_path_to_module_fqn(rel_path)
        if not fqn:
            continue
        source = py_file.read_bytes()
        line_count = source.count(b"\n") + (0 if source.endswith(b"\n") else 1) or 1
        nodes.append(FQNNode(
            fqn=fqn,
            kind="module",
            file_path=rel_path,
            line_start=0,
            line_end=line_count - 1,
        ))
        module_fqns[fqn] = rel_path
    
    # parse each file to extract class/function/method nodes + CONTAIN edges
    file_source: dict[str, bytes] = {}
    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        fqn = file_path_to_module_fqn(rel_path)
        if not fqn:
            continue
        source = py_file.read_bytes()
        file_source[fqn] = source
        file_nodes, file_edges = parse_file(py_file, fqn, rel_path, source)
        nodes.extend(file_nodes)
        edges.extend(file_edges)

    return ADG(nodes=nodes, edges=edges)


if __name__ == "__main__":
    import os
    repo = Path("../../repos/flask")
    print(parse_repo(repo))