"""Diff Processor: identify changed FQNs from a git commit diff."""

from services.fqn import FQN
from services.models import ADG, ChangedFQN, CommitDiff, DiffResult, FileChange, FQNKind
from services.adg import parse_file

def process_diff(commit_diff: CommitDiff) -> DiffResult:
    """Process a CommitDiff and return changed FQNs and file changes"""
    changed_fqns: list[ChangedFQN] = []

    for file_change in commit_diff.changed_files:
        path = file_change.path

        if not path.endswith(".py"):
            continue

        status = file_change.status
        module_fqn = FQN.from_path(path)
        before = len(changed_fqns)

        if status == "added":
            source = commit_diff.file_contents.get(path, b"")
            if source:
                nodes, _ = parse_file(source, module_fqn, path)
                for node in nodes:
                    changed_fqns.append(
                        make_changed_fqn(node, "added", path, module_fqn)
                    )
        elif status == "deleted":
            source = commit_diff.parent_contents.get(path, b"")
            if source:
                nodes, _ = parse_file(source, module_fqn, path)
                for node in nodes:
                    changed_fqns.append(
                        make_changed_fqn(node, "deleted", path, module_fqn)
                    )

        elif status == "modified":
            old_source = commit_diff.parent_contents.get(path, b"")
            new_source = commit_diff.file_contents.get(path, b"")
            old_nodes, _ = parse_file(old_source, module_fqn, path)
            new_nodes, _ = parse_file(new_source, module_fqn, path)

            old_map = {n.fqn: n for n in old_nodes}
            new_map = {n.fqn: n for n in new_nodes}
            old_set = set(old_map)
            new_set = set(new_map)

            for fqn in old_set - new_set:
                changed_fqns.append(
                    make_changed_fqn(old_map[fqn], "deleted", path, module_fqn)
                )

            for fqn in new_set - old_set:
                changed_fqns.append(
                    make_changed_fqn(new_map[fqn], "added", path, module_fqn)
                )

            for fqn in old_set & new_set:
                old_node = old_map[fqn]
                new_node = new_map[fqn]
                old_content = old_source[old_node.start_byte:old_node.end_byte]
                new_content = new_source[new_node.start_byte:new_node.end_byte]
                if old_content != new_content:
                    changed_fqns.append(
                        make_changed_fqn(new_node, "modified", path, module_fqn)
                    )

        elif status == "renamed":
            old_path = file_change.old_path or path
            old_module = FQN.from_path(old_path)

            old_source = commit_diff.parent_contents.get(old_path, b"")
            if old_source:
                nodes, _ = parse_file(old_source, old_module, old_path)
                for node in nodes:
                    changed_fqns.append(
                        make_changed_fqn(node, "deleted", old_path, old_module)
                    )

            new_source = commit_diff.file_contents.get(path, b"")
            if new_source:
                nodes, _ = parse_file(new_source, module_fqn, path)
                for node in nodes:
                    changed_fqns.append(
                        make_changed_fqn(node, "added", path, module_fqn)
                    )

        # ponytail: if no def-level FQN was emitted for this .py file but the
        # file bytes actually changed, emit a module-level ChangedFQN so BFS
        # still has a starting point. Covers settings.py / config.py edits that
        # only touch module-level assignments.
        if len(changed_fqns) == before:
            _maybe_emit_module_fqn(changed_fqns, file_change, module_fqn, commit_diff)

    return DiffResult(
        commit_sha=commit_diff.commit_sha,
        parent_sha=commit_diff.parent_sha,
        changed_files=commit_diff.changed_files,
        changed_fqns=changed_fqns,
    )

def make_changed_fqn(
        node, change_type:str, file_path: str, module_fqn: FQN
) -> ChangedFQN:
    """Derive enclosing scope from an FQNNode and create a ChangedFQN"""
    enclosing_class = None
    if node.kind == FQNKind.METHOD:
        enclosing_class = node.fqn.parent

    return ChangedFQN(
          fqn=node.fqn,
          change_type=change_type,
          file_path=file_path,
          enclosing_class=enclosing_class,
          enclosing_module=module_fqn,
      )


def _maybe_emit_module_fqn(
        changed_fqns: list[ChangedFQN],
        file_change: FileChange,
        module_fqn: FQN,
        commit_diff: CommitDiff,
) -> None:
    """Emit a module-level ChangedFQN if the .py file bytes changed but no def FQN was produced."""
    status = file_change.status
    path = file_change.path
    new_src = commit_diff.file_contents.get(path, b"")
    old_src = commit_diff.parent_contents.get(path, b"")

    if status == "added" and new_src:
        change_type = "added"
    elif status == "deleted" and old_src:
        change_type = "deleted"
    elif status == "modified" and new_src != old_src:
        change_type = "modified"
    elif status == "renamed":
        old_path = file_change.old_path or path
        if commit_diff.parent_contents.get(old_path, b"") or new_src:
            change_type = "added"
        else:
            return
    else:
        return

    changed_fqns.append(
        ChangedFQN(
            fqn=module_fqn,
            change_type=change_type,
            file_path=path,
            enclosing_class=None,
            enclosing_module=module_fqn,
        )
    )


def augment_adg(adg: ADG, commit_diff: CommitDiff) -> None:
    """Merge new/modified file contents from a diff into the ADG in-place.

    Without this, BFS from added FQNs can't expand because those nodes
    don't exist in the base ADG.
    """
    from tree_sitter import Parser
    from services.adg.treesitter import PY_LANGUAGE, walk_imports, walk_calls, walk_inherits, parse_file
    from services.resolver import NameResolver

    existing_fqns = {n.fqn for n in adg.nodes}
    existing_edges = {(e.source, e.target, e.kind) for e in adg.edges}
    diff_sources: dict[FQN, bytes] = {}

    for file_change in commit_diff.changed_files:
        path = file_change.path
        if not path.endswith(".py"):
            continue
        # Use new content for added/modified, old content for deleted
        if file_change.status in ("added", "modified"):
            source = commit_diff.file_contents.get(path, b"")
        elif file_change.status == "renamed":
            source = commit_diff.file_contents.get(path, b"")
        else:
            continue
        if not source:
            continue

        module_fqn = FQN.from_path(path)
        diff_sources[module_fqn] = source

        # Ensure module_fqn and all parent packages exist in adg.nodes
        from services.models import FQNNode, FQNKind, Edge
        for i in range(1, len(module_fqn.parts) + 1):
            parent_fqn = FQN.from_dotted_safe(".".join(module_fqn.parts[:i]))
            if parent_fqn is not None and parent_fqn not in existing_fqns:
                adg.nodes.append(FQNNode(
                    fqn=parent_fqn,
                    kind=FQNKind.MODULE,
                    file_path=path if i == len(module_fqn.parts) else "",
                    line_start=0,
                    line_end=0,
                    start_byte=0,
                    end_byte=len(source) if i == len(module_fqn.parts) else 0,
                ))
                existing_fqns.add(parent_fqn)

        # Ensure CONTAINS edges exist for the package hierarchy
        for i in range(2, len(module_fqn.parts) + 1):
            parent_fqn = FQN.from_dotted_safe(".".join(module_fqn.parts[:i-1]))
            child_fqn = FQN.from_dotted_safe(".".join(module_fqn.parts[:i]))
            if parent_fqn is not None and child_fqn is not None:
                key = (str(parent_fqn), str(child_fqn), "CONTAINS")
                if key not in existing_edges:
                    adg.edges.append(Edge(source=str(parent_fqn), target=str(child_fqn), kind="CONTAINS"))
                    existing_edges.add(key)

        new_nodes, new_edges = parse_file(source, module_fqn, path)
        for node in new_nodes:
            if node.fqn not in existing_fqns:
                adg.nodes.append(node)
                existing_fqns.add(node.fqn)
        for edge in new_edges:
            key = (edge.source, edge.target, edge.kind)
            if key not in existing_edges:
                adg.edges.append(edge)
                existing_edges.add(key)

    # Pass 2: Extract IMPORTS, CALLS, and INHERITS edges for the diff files
    parser = Parser(PY_LANGUAGE)
    resolver = NameResolver(existing_fqns)
    new_dep_edges: list[Edge] = []

    for fqn, source in diff_sources.items():
        tree = parser.parse(source)
        root = tree.root_node
        walk_imports(root, fqn, existing_fqns, new_dep_edges)
        walk_calls(root, fqn, resolver, new_dep_edges)
        walk_inherits(root, fqn, resolver, new_dep_edges)

    for edge in new_dep_edges:
        key = (edge.source, edge.target, edge.kind)
        if key not in existing_edges:
            adg.edges.append(edge)
            existing_edges.add(key)
            if edge.kind == "IMPORTS":
                target_fqn = FQN.from_dotted_safe(edge.target)
                if target_fqn is not None and target_fqn not in existing_fqns:
                    adg.nodes.append(FQNNode(
                        fqn=target_fqn,
                        kind=FQNKind.EXTERNAL,
                        file_path="",
                        line_start=-1,
                        line_end=-1,
                    ))
                    existing_fqns.add(target_fqn)