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

def walk_definitions(node, parent_fqn: str, parent_kind: str, rel_path: str, nodes: list[FQNNode], edges: list[Edge]):
    """Recursively walk AST to extract class, function, method definitions."""
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
            walk_definitions(child, class_fqn, "class", rel_path, nodes, edges)

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
            line_end=node.end_point[0],
        ))
        edges.append(Edge(source=parent_fqn, target=func_fqn, kind="CONTAINS"))
        for child in node.children:
            walk_definitions(child, func_fqn, kind, rel_path, nodes, edges)

    elif node.type == "decorated_definition":
        for child in node.children:
            walk_definitions(child, parent_fqn, parent_kind, rel_path, nodes, edges)

    else:
        for child in node.children:
            walk_definitions(child, parent_fqn, parent_kind, rel_path, nodes, edges)


def walk_imports(node, module_fqn: str, known_fqns: set[str], edges: list[Edge]):
    """Recursively walk AST to extract IMPORTS edges from import/from..import statements."""

    if node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return
        module_name = module_node.text.decode("utf-8")

        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                imported = child.text.decode("utf-8")
                target_fqn = f"{module_name}.{imported}"
                if target_fqn in known_fqns:
                    edges.append(Edge(source=module_fqn, target=target_fqn, kind="IMPORTS"))
                elif module_name in known_fqns:
                    edges.append(Edge(source=module_fqn, target=module_name, kind="IMPORTS"))

            elif child.type == "import_list":
                for name_node in child.children:
                    if name_node.type == "dotted_name":
                        imported = name_node.text.decode("utf-8")
                        target_fqn = f"{module_name}.{imported}"
                        if target_fqn in known_fqns:
                            edges.append(Edge(source=module_fqn, target=target_fqn, kind="IMPORTS"))
                        elif module_name in known_fqns:
                            edges.append(Edge(source=module_fqn, target=module_name, kind="IMPORTS"))
                    elif name_node.type == "wildcard_import":
                        if module_name in known_fqns:
                            edges.append(Edge(source=module_fqn, target=module_name, kind="IMPORTS"))

    elif node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                imported = child.text.decode("utf-8")
                if imported in known_fqns:
                    edges.append(Edge(source=module_fqn, target=imported, kind="IMPORTS"))

    # recurse into children for all node types
    for child in node.children:
        walk_imports(child, module_fqn, known_fqns, edges)


def parse_file(py_file: Path, module_fqn: str, rel_path: str, source: bytes, known_fqns: set[str]) -> tuple[list[FQNNode], list[Edge]]:
    """Parse a single Python file and return FQN nodes and edges."""
    parser = Parser(PY_LANGUGAGE)
    tree = parser.parse(source)
    root = tree.root_node

    nodes: list[FQNNode] = []
    edges: list[Edge] = []

    for child in root.children:
        walk_definitions(child, module_fqn, "module", rel_path, nodes, edges)

    walk_imports(root, module_fqn, known_fqns, edges)

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
    
    # parse each file to extract class/function/method nodes + CONTAIN/IMPORTS edges
    known_fqns = set(module_fqns.keys())
    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        fqn = file_path_to_module_fqn(rel_path)
        if not fqn:
            continue
        source = py_file.read_bytes()
        file_nodes, file_edges = parse_file(py_file, fqn, rel_path, source, known_fqns)
        nodes.extend(file_nodes)
        edges.extend(file_edges)

    return ADG(nodes=nodes, edges=edges)


if __name__ == "__main__":
    repo = Path("../../repos/flask")
    adg = parse_repo(repo)
    print(f"Nodes: {len(adg.nodes)}, Edges: {len(adg.edges)}")
    print(f"  Node kinds: {{{', '.join(sorted(set(n.kind for n in adg.nodes)))}}}")
    print(f"  Edge kinds: {{{', '.join(sorted(set(e.kind for e in adg.edges)))}}}")
    imports = [e for e in adg.edges if e.kind == "IMPORTS"]
    contains = [e for e in adg.edges if e.kind == "CONTAINS"]
    print(f"  IMPORTS edges: {len(imports)}")
    print(f"  CONTAINS edges: {len(contains)}")
    # for node in adg.nodes:
    #     print(node)
    # for edge in adg.edges:
    #     print(edge)