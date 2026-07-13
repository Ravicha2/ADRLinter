"""Merge Layer: unify Track A (AST) ADG with Track B (ADR symbolic constraints).

Delegates symbolic resolution to symbolic_resolver, then merges the resulting
ConstraintEdges into the ADG.
"""
from __future__ import annotations

import configparser
import logging
import re
from pathlib import Path

from services.fqn import FQN
from services.models import (
    ADG,
    DependencyRole,
    FQNKind,
    FQNNode,
    SymbolicConstraint,
)
from services.adg.symbolic_resolver import resolve_symbolic_constraints

log = logging.getLogger(__name__)

PYTHON_DEV_TOOLS: frozenset[str] = frozenset({
    "pytest", "py", "nose", "nose2", "unittest",
    "black", "autopep8", "yapf",
    "mypy", "pyre", "pytype",
    "flake8", "pylint", "pyflakes", "pydocstyle", "ruff",
    "isort", "bandit",
    "tox", "nox",
    "coverage", "pytest_cov",
    "sphinx", "mkdocs",
    "setuptools", "wheel", "pip", "build",
    "twine", "pre_commit",
})

_DEV_EXTRA_NAMES: frozenset[str] = frozenset({
    "dev", "development", "dev-dependencies",
    "test", "tests", "testing",
    "lint", "linting",
    "typing", "types",
    "docs", "documentation",
})

_PACKAGE_NAME_RE = re.compile(r"^([a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)")


def _extract_package_name(dep_spec: str) -> str:
    """Extract normalized package name from a PEP 508 dependency spec.

    "pytest>=8.2" -> "pytest", "python-dateutil" -> "python_dateutil"
    """
    dep_spec = dep_spec.strip()
    if not dep_spec:
        return ""
    match = _PACKAGE_NAME_RE.match(dep_spec)
    if not match:
        return ""
    return match.group(1).lower().replace("-", "_").replace(".", "_")


def _load_dev_packages_from_config(project_root: Path | None) -> frozenset[str]:
    """Parse pyproject.toml or setup.cfg for dev/test/lint dependency extras.

    Best-effort: missing or malformed files return an empty frozenset.
    pyproject.toml takes priority; setup.cfg is only consulted if
    pyproject.toml has no dev extras.
    """
    if project_root is None:
        return frozenset()

    packages: set[str] = set()

    # Try pyproject.toml first (Python 3.11+ has tomllib)
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            import tomllib
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            extras = data.get("project", {}).get("optional-dependencies", {})
            for group_name, deps in extras.items():
                if group_name in _DEV_EXTRA_NAMES:
                    for dep in deps:
                        name = _extract_package_name(dep)
                        if name:
                            packages.add(name)
        except Exception:
            pass  # best-effort

    if packages:
        return frozenset(packages)

    # Fallback to setup.cfg
    setup_cfg_path = project_root / "setup.cfg"
    if setup_cfg_path.exists():
        try:
            config = configparser.ConfigParser()
            config.read(setup_cfg_path)
            if config.has_section("options.extras_require"):
                for group_name in config["options.extras_require"]:
                    if group_name in _DEV_EXTRA_NAMES:
                        for line in config["options.extras_require"][group_name].splitlines():
                            name = _extract_package_name(line)
                            if name:
                                packages.add(name)
        except Exception:
            pass  # best-effort

    return frozenset(packages)


def _classify_external_role(
    fqn_str: str,
    extra_dev_packages: frozenset[str] = frozenset(),
) -> DependencyRole:
    """Classify an external FQN by its root package name.

    Hardcoded registry takes priority over project config.
    """
    root_package = fqn_str.split(".")[0]
    if root_package in PYTHON_DEV_TOOLS:
        return DependencyRole.DEV_TOOL
    if root_package in extra_dev_packages:
        return DependencyRole.DEV_TOOL
    return DependencyRole.UNKNOWN


def add_external_nodes(adg: ADG, project_root: Path | None = None) -> ADG:
    """Create EXTERNAL nodes for import targets not defined in the repo.

    project_root: optional path to repo root for dev-tool classification
                  via pyproject.toml / setup.cfg extras.
    """
    extra_dev_packages = _load_dev_packages_from_config(project_root)

    known_fqns = {str(node.fqn) for node in adg.nodes}
    import_targets = {edge.target for edge in adg.edges if edge.kind == "IMPORTS"}

    external_fqns = sorted(import_targets - known_fqns)
    if external_fqns:
        log.info("add_external_nodes: creating %d EXTERNAL nodes for unresolved imports: %s", len(external_fqns), external_fqns)
    else:
        log.debug("add_external_nodes: no unresolved imports")
    external_nodes = [
        FQNNode(
            fqn=FQN.from_dotted(fqn),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
            role=_classify_external_role(fqn, extra_dev_packages),
        )
        for fqn in external_fqns
    ]

    return ADG(nodes=adg.nodes + external_nodes, edges=adg.edges, constraint_edges=adg.constraint_edges)


def merge_constraints(adg: ADG, constraints: list[SymbolicConstraint], project_root: Path | None = None) -> ADG:
    """Unify Track A ADG + Track B symbolic constraints into a merged ADG.

    Resolves SymbolicConstraints against ADG nodes, produces ConstraintEdges,
    and adds them to the ADG along with any needed EXTERNAL nodes.

    project_root: optional path to repo root for dev-tool classification
                  via pyproject.toml / setup.cfg extras.
    """
    log.info("merge_constraints: merging %d symbolic constraints into ADG with %d nodes", len(constraints), len(adg.nodes))

    extra_dev_packages = _load_dev_packages_from_config(project_root)
    resolved = resolve_symbolic_constraints(constraints, adg, project_root=project_root)

    constraint_edges = [rc.constraint_edge for rc in resolved]

    # Collect all FQNs from the ADG nodes (including EXTERNAL nodes added
    # during resolution)
    all_adg_nodes = set()
    for rc in resolved:
        all_adg_nodes.add(rc.constraint_edge.subject)
        all_adg_nodes.add(rc.constraint_edge.object)

    known_fqns = {str(n.fqn) for n in adg.nodes}

    # Add EXTERNAL nodes for any remaining orphans
    orphan_fqns = sorted(all_adg_nodes - known_fqns)
    external_nodes = [
        FQNNode(
            fqn=FQN.from_dotted(fqn),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
            role=_classify_external_role(fqn, extra_dev_packages),
        )
        for fqn in orphan_fqns
    ]
    if external_nodes:
        log.info("merge_constraints: adding %d EXTERNAL nodes for orphans: %s", len(external_nodes), orphan_fqns)

    return ADG(
        nodes=adg.nodes + external_nodes,
        edges=adg.edges,
        constraint_edges=adg.constraint_edges + constraint_edges,
    )