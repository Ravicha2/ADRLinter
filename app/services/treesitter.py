"""Track A: parse Python repositories into Architectural Decision Graph nodes and edges."""

from __future__ import annotations

from pathlib import Path
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from services.fqn import FQN
from services.models import ADG, Edge, FQNKind, FQNNode

PY_LANGUAGE = Language(tspython.language())

def walk_definitions(node, parent_fqn: FQN, parent_kind: str, rel_path: str, nodes: list[FQNNode], edges: list[Edge]):
    """Recursively walk AST to extract class, function, method definitions."""
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = name_node.text.decode("utf-8")
        class_fqn = parent_fqn.child(class_name)
        nodes.append(FQNNode(
            fqn=class_fqn,
            kind=FQNKind.CLASS,
            file_path=rel_path,
            line_start=node.start_point[0],
            line_end=node.end_point[0],
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        ))
        edges.append(Edge(source=str(parent_fqn), target=str(class_fqn), kind="CONTAINS"))
        for child in node.children:
            walk_definitions(child, class_fqn, "class", rel_path, nodes, edges)

    elif node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = name_node.text.decode("utf-8")
        func_fqn = parent_fqn.child(func_name)
        kind = FQNKind.METHOD if parent_kind == "class" else FQNKind.FUNCTION
        nodes.append(FQNNode(
            fqn=func_fqn,
            kind=kind,
            file_path=rel_path,
            line_start=node.start_point[0],
            line_end=node.end_point[0],
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        ))
        edges.append(Edge(source=str(parent_fqn), target=str(func_fqn), kind="CONTAINS"))
        for child in node.children:
            walk_definitions(child, func_fqn, kind.value, rel_path, nodes, edges)

    elif node.type == "decorated_definition":
        for child in node.children:
            walk_definitions(child, parent_fqn, parent_kind, rel_path, nodes, edges)

    else:
        for child in node.children:
            walk_definitions(child, parent_fqn, parent_kind, rel_path, nodes, edges)


def walk_imports(node, module_fqn: FQN, known_fqns: set[FQN], edges: list[Edge]):
    """Recursively walk AST to extract IMPORTS edges from import/from..import statements."""

    if node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node is not None:
            module_name = module_node.text.decode("utf-8")

            for child in node.children:
                if child.type == "dotted_name" and child != module_node:
                    imported = child.text.decode("utf-8")
                    target_fqn = FQN.from_dotted(f"{module_name}.{imported}")
                    if target_fqn in known_fqns:
                        edges.append(Edge(source=str(module_fqn), target=str(target_fqn), kind="IMPORTS"))
                    elif FQN.from_dotted_safe(module_name) in known_fqns:
                        edges.append(Edge(source=str(module_fqn), target=module_name, kind="IMPORTS"))

                elif child.type == "import_list":
                    for name_node in child.children:
                        if name_node.type == "dotted_name":
                            imported = name_node.text.decode("utf-8")
                            target_fqn = FQN.from_dotted(f"{module_name}.{imported}")
                            if target_fqn in known_fqns:
                                edges.append(Edge(source=str(module_fqn), target=str(target_fqn), kind="IMPORTS"))
                            elif FQN.from_dotted_safe(module_name) in known_fqns:
                                edges.append(Edge(source=str(module_fqn), target=module_name, kind="IMPORTS"))
                        elif name_node.type == "aliased_import":
                            real_name = name_node.child_by_field_name("name")
                            if real_name is not None:
                                imported = real_name.text.decode("utf-8")
                                target_fqn = FQN.from_dotted(f"{module_name}.{imported}")
                                if target_fqn in known_fqns:
                                    edges.append(Edge(source=str(module_fqn), target=str(target_fqn), kind="IMPORTS"))
                                elif FQN.from_dotted_safe(module_name) in known_fqns:
                                    edges.append(Edge(source=str(module_fqn), target=module_name, kind="IMPORTS"))
                        elif name_node.type == "wildcard_import":
                            if FQN.from_dotted_safe(module_name) in known_fqns:
                                edges.append(Edge(source=str(module_fqn), target=module_name, kind="IMPORTS"))
        return

    elif node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                imported = child.text.decode("utf-8")
                if FQN.from_dotted_safe(imported) in known_fqns:
                    edges.append(Edge(source=str(module_fqn), target=imported, kind="IMPORTS"))
            elif child.type == "aliased_import":
                real_name = child.child_by_field_name("name")
                if real_name is not None:
                    imported = real_name.text.decode("utf-8")
                    if FQN.from_dotted_safe(imported) in known_fqns:
                        edges.append(Edge(source=str(module_fqn), target=imported, kind="IMPORTS"))
        return

    # recurse into children for all other node types
    for child in node.children:
        walk_imports(child, module_fqn, known_fqns, edges)

def walk_calls(node, caller_fqn: FQN, known_fqns: set[FQN], edges: list[Edge]):
    """Recursively walk AST to extract CALLS edges from function call expressions."""
    if node.type == "call":
        callee_node = node.child(0)
        if callee_node is not None:
            callee_text = callee_node.text.decode("utf-8")
            resolved = resolve_call(callee_text, caller_fqn, known_fqns)
            if resolved is not None:
                edges.append(Edge(source=str(caller_fqn), target=str(resolved), kind="CALLS"))

    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            func_name = name_node.text.decode("utf-8")
            inner_fqn = caller_fqn.child(func_name)
            if inner_fqn in known_fqns:
                caller_fqn = inner_fqn

    for child in node.children:
        walk_calls(child, caller_fqn, known_fqns, edges)


def resolve_call(callee_text: str, caller_fqn: FQN, known_fqns: set[FQN]) -> FQN | None:
    """resolve a callee expression to a known FQN."""
    for fqn in known_fqns:
        fqn_str = str(fqn)
        if fqn_str.endswith(f".{callee_text}") or fqn_str == callee_text:
            return fqn
    return None

def walk_inherits(node, module_fqn: FQN, known_fqns: set[FQN], edges: list[Edge]):
    """Recursively walk AST to extract INHERITS edges from class definitions."""
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = name_node.text.decode("utf-8")
        class_fqn = module_fqn.child(class_name)

        superclasses = node.child_by_field_name("superclasses")
        if superclasses is not None:
            for child in superclasses.children:
                if child.type in ("identifier", "attribute", "dotted_name"):
                    base_text = child.text.decode("utf-8")
                    resolved = resolve_base_class(base_text, known_fqns)
                    if resolved is not None and class_fqn in known_fqns:
                        edges.append(Edge(source=str(class_fqn), target=str(resolved), kind="INHERITS"))
        # recurse into class body for nested classes
        for child in node.children:
            walk_inherits(child, module_fqn, known_fqns, edges)

    elif node.type == "decorated_definition":
        for child in node.children:
            walk_inherits(child, module_fqn, known_fqns, edges)

    else:
        for child in node.children:
            walk_inherits(child, module_fqn, known_fqns, edges)

def resolve_base_class(base_text: str, known_fqns: set[FQN]) -> FQN | None:
    """Resolve a base class reference to a known FQN"""
    for fqn in known_fqns:
        fqn_str = str(fqn)
        if fqn_str == base_text or fqn_str.endswith(f".{base_text}"):
            return fqn
    return None

def parse_file(source: bytes, module_fqn: FQN, rel_path: str) -> tuple[list[FQNNode], list[Edge]]:
    """Parse a single .py file and extract FQN definition nodes and CONTAINS edges."""
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)

    # fail fast
    if _has_error(tree.root_node):
        raise SyntaxError(f"Syntax error in {rel_path}")

    nodes: list[FQNNode] = []
    edges: list[Edge] = []
    for child in tree.root_node.children:
        walk_definitions(child, module_fqn, "module", rel_path, nodes, edges)

    return nodes, edges

def _has_error(node) -> bool:
    """Check if the AST has any errors."""
    return node.has_error



def parse_repo(repo_path: Path) -> ADG:
    """
    Walk all .py files in repo_path and extract FQN nodes and edges.
    """
    nodes: list[FQNNode] = []
    edges: list[Edge] = []

    py_files = sorted(repo_path.rglob("*.py"))
    parser = Parser(PY_LANGUAGE)

    # Pass 1: read sources + create module nodes
    file_sources: dict[FQN, bytes] = {}
    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        fqn = FQN.from_path(rel_path)
        source = py_file.read_bytes()
        file_sources[fqn] = source
        line_count = source.count(b"\n") + (0 if source.endswith(b"\n") else 1) or 1
        nodes.append(FQNNode(
            fqn=fqn,
            kind=FQNKind.MODULE,
            file_path=rel_path,
            line_start=0,
            line_end=line_count - 1,
            start_byte=0,
            end_byte=len(source)
        ))

    # Pass 2: extract class/function/method definitions + CONTAINS edges
    for fqn, source in file_sources.items():
        rel_path = next(n.file_path for n in nodes if n.fqn == fqn)
        file_nodes, file_edges = parse_file(source, fqn, rel_path)
        nodes.extend(file_nodes)
        edges.extend(file_edges)

    known_fqns = {n.fqn for n in nodes}

    # Pass 3: resolve IMPORTS, CALLS, INHERITS edges
    for fqn, source in file_sources.items():
        tree = parser.parse(source)
        root = tree.root_node

        walk_imports(root, fqn, known_fqns, edges)
        walk_calls(root, fqn, known_fqns, edges)
        walk_inherits(root, fqn, known_fqns, edges)

    return ADG(nodes=nodes, edges=edges)


if __name__ == "__main__":
    repo = Path("../../repos/flask")
    adg = parse_repo(repo)
    print(f"Nodes: {len(adg.nodes)}, Edges: {len(adg.edges)}")
    print(f"  Node kinds: {{{', '.join(sorted(set(n.kind.value for n in adg.nodes)))}}}")
    print(f"  Edge kinds: {{{', '.join(sorted(set(e.kind for e in adg.edges)))}}}")
    imports = [e for e in adg.edges if e.kind == "IMPORTS"]
    contains = [e for e in adg.edges if e.kind == "CONTAINS"]
    print(f"  IMPORTS edges: {len(imports)}")
    print(f"  CONTAINS edges: {len(contains)}")