"""Tests for the tree-sitter service: parse_repo -> ADG with FQN nodes and edges.

Public interface under test:
    parse_repo(repo_path: Path) -> ADG

Each test class covers one behavioral aspect of the tree-sitter parser,
using the sample_repo fixture for deterministic, controlled inputs and
the flask_repo fixture for integration testing against real code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.fqn import FQN
from services.models import ADG, Edge, FQNKind, FQNNode
from services.treesitter import parse_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_node(adg: ADG, fqn: str) -> FQNNode | None:
    """find FQN node"""
    target = FQN.from_dotted(fqn)
    for n in adg.nodes:
        if n.fqn == target:
            return n
    return None


def _find_nodes(adg: ADG, kind: FQNKind) -> list[FQNNode]:
    """find FQN nodes"""
    return [n for n in adg.nodes if n.kind == kind]


def _find_edge(adg: ADG, source: str, target: str, kind: str) -> Edge | None:
    """find edge that have source and target"""
    for e in adg.edges:
        if e.source == source and e.target == target and e.kind == kind:
            return e
    return None


def _find_edges(adg: ADG, kind: str) -> list[Edge]:
    """find edges that have source and target"""
    return [e for e in adg.edges if e.kind == kind]


# ===========================================================================
# 1. Class, function, and method extraction
# ===========================================================================


class TestClassFunctionMethodExtraction:
    """Class definitions, top-level functions, and methods appear as FQNNodes."""

    def test_class_node(self, sample_repo: Path) -> None:
        """class User in app/models/user.py -> node 'app.models.user.User' kind=CLASS."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.models.user.User")
        assert node is not None
        assert node.kind == FQNKind.CLASS
        assert node.file_path == "app/models/user.py"

    def test_base_class_node(self, sample_repo: Path) -> None:
        """class BaseModel in app/models/base.py -> node 'app.models.base.BaseModel' kind=CLASS."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.models.base.BaseModel")
        assert node is not None
        assert node.kind == FQNKind.CLASS

    def test_method_nodes(self, sample_repo: Path) -> None:
        """Methods inside a class are extracted with kind=METHOD."""
        adg = parse_repo(sample_repo)
        find_node = _find_node(adg, "app.models.user.User.find")
        assert find_node is not None
        assert find_node.kind == FQNKind.METHOD

        all_node = _find_node(adg, "app.models.user.User.all")
        assert all_node is not None
        assert all_node.kind == FQNKind.METHOD

    def test_base_class_methods(self, sample_repo: Path) -> None:
        """BaseModel has save and delete methods."""
        adg = parse_repo(sample_repo)
        assert _find_node(adg, "app.models.base.BaseModel.save") is not None
        assert _find_node(adg, "app.models.base.BaseModel.delete") is not None

    def test_top_level_function(self, sample_repo: Path) -> None:
        """Top-level function get_user -> kind=FUNCTION (not METHOD)."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.services.user_service.get_user")
        assert node is not None
        assert node.kind == FQNKind.FUNCTION

    def test_class_line_range(self, sample_repo: Path) -> None:
        """Class node line_start/line_end span the entire class definition."""
        adg = parse_repo(sample_repo)
        user_class = _find_node(adg, "app.models.user.User")
        assert user_class is not None
        assert user_class.line_start >= 0
        assert user_class.line_end > user_class.line_start

    def test_method_line_range(self, sample_repo: Path) -> None:
        """Method node line ranges fall within the parent class range."""
        adg = parse_repo(sample_repo)
        user_class = _find_node(adg, "app.models.user.User")
        find_method = _find_node(adg, "app.models.user.User.find")
        assert user_class is not None
        assert find_method is not None
        assert find_method.line_start >= user_class.line_start
        assert find_method.line_end <= user_class.line_end

    def test_no_duplicates(self, sample_repo: Path) -> None:
        """Each FQN appears at most once in the node list."""
        adg = parse_repo(sample_repo)
        fqns = [n.fqn for n in adg.nodes]
        assert len(fqns) == len(set(fqns))


# ===========================================================================
# 2. Module node properties
# ===========================================================================


class TestModuleNodes:
    """Module nodes have correct FQNs derived from file paths."""

    def test_regular_module_fqn(self, sample_repo: Path) -> None:
        """app/services/user_service.py -> module node with fqn 'app.services.user_service'."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.services.user_service")
        assert node is not None
        assert node.kind == FQNKind.MODULE
        assert node.file_path == "app/services/user_service.py"

    def test_init_py_produces_parent_package_fqn(self, sample_repo: Path) -> None:
        """app/models/__init__.py -> module node with fqn 'app.models'."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.models")
        assert node is not None
        assert node.kind == FQNKind.MODULE
        assert node.file_path == "app/models/__init__.py"

    def test_top_level_init_fqn(self, sample_repo: Path) -> None:
        """app/__init__.py -> module node with fqn 'app'."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app")
        assert node is not None
        assert node.kind == FQNKind.MODULE
        assert node.file_path == "app/__init__.py"

    def test_config_module_fqn(self, sample_repo: Path) -> None:
        """app/config.py -> module node with fqn 'app.config'."""
        adg = parse_repo(sample_repo)
        node = _find_node(adg, "app.config")
        assert node is not None
        assert node.kind == FQNKind.MODULE
        assert node.file_path == "app/config.py"

    def test_module_node_has_line_range(self, sample_repo: Path) -> None:
        """Every module node has valid line_start and line_end."""
        adg = parse_repo(sample_repo)
        modules = _find_nodes(adg, FQNKind.MODULE)
        assert len(modules) > 0
        for m in modules:
            assert m.line_start >= 0
            assert m.line_end >= m.line_start

    def test_no_duplicate_module_nodes(self, sample_repo: Path) -> None:
        """Each file produces exactly one module node."""
        adg = parse_repo(sample_repo)
        module_fqns = [n.fqn for n in _find_nodes(adg, FQNKind.MODULE)]
        assert len(module_fqns) == len(set(module_fqns))


# ===========================================================================
# 3. CONTAINS edges
# ===========================================================================


class TestContainsEdges:
    """CONTAINS edges connect module->class/function and class->method."""

    def test_module_contains_class(self, sample_repo: Path) -> None:
        """Module app.models.user CONTAINS class User."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.user", "app.models.user.User", "CONTAINS") is not None

    def test_module_contains_function(self, sample_repo: Path) -> None:
        """Module app.services.user_service CONTAINS function get_user."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.services.user_service", "app.services.user_service.get_user", "CONTAINS") is not None

    def test_class_contains_method(self, sample_repo: Path) -> None:
        """Class User CONTAINS method find."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.user.User", "app.models.user.User.find", "CONTAINS") is not None

    def test_class_contains_all_methods(self, sample_repo: Path) -> None:
        """Class User CONTAINS both find and all methods."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.user.User", "app.models.user.User.find", "CONTAINS") is not None
        assert _find_edge(adg, "app.models.user.User", "app.models.user.User.all", "CONTAINS") is not None

    def test_base_model_contains_methods(self, sample_repo: Path) -> None:
        """BaseModel CONTAINS save and delete."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.base.BaseModel", "app.models.base.BaseModel.save", "CONTAINS") is not None
        assert _find_edge(adg, "app.models.base.BaseModel", "app.models.base.BaseModel.delete", "CONTAINS") is not None


# ===========================================================================
# 4. IMPORTS edges
# ===========================================================================


class TestImportsEdges:
    """IMPORTS edges are derived from import/from-import statements.

    Only resolve imports that match FQNs within the repo being parsed.
    stdlib and third-party imports are skipped.
    """

    def test_from_import_resolves_to_fqn(self, sample_repo: Path) -> None:
        """'from app.models.user import User' -> IMPORTS edge to app.models.user.User."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.services.user_service", "app.models.user.User", "IMPORTS") is not None

    def test_from_import_in_init(self, sample_repo: Path) -> None:
        """'from app.config import DEBUG' in app/__init__.py -> IMPORTS edge."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app", "app.config", "IMPORTS") is not None

    def test_from_import_resolves_to_module(self, sample_repo: Path) -> None:
        """'from app.models.user import User' in app/__init__.py -> IMPORTS to app.models.user.User."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app", "app.models.user.User", "IMPORTS") is not None

    def test_base_model_import_edge(self, sample_repo: Path) -> None:
        """'from app.models.base import BaseModel' in user.py -> IMPORTS edge."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.user", "app.models.base.BaseModel", "IMPORTS") is not None

    def test_unresolvable_imports_skipped(self, sample_repo: Path) -> None:
        """Imports of stdlib/third-party modules are not included as edges."""
        adg = parse_repo(sample_repo)
        import_targets = {e.target for e in _find_edges(adg, "IMPORTS")}
        for target in import_targets:
            assert not target.startswith(("os.", "sys.", "json.", "collections.")), (
                f"stdlib import target should be skipped: {target}"
            )


# ===========================================================================
# 5. CALLS edges
# ===========================================================================


class TestCallsEdges:
    """CALLS edges are derived from function call expressions.

    Only resolve calls to FQNs within the repo.
    """

    def test_method_call_resolved(self, sample_repo: Path) -> None:
        """User.find() call in get_user -> CALLS edge from get_user to User.find."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.services.user_service.get_user", "app.models.user.User.find", "CALLS") is not None

    def test_no_calls_to_unknown_targets(self, sample_repo: Path) -> None:
        """CALLS edges only target FQNs that exist in the repo."""
        adg = parse_repo(sample_repo)
        all_fqns = {str(n.fqn) for n in adg.nodes}
        calls_edges = _find_edges(adg, "CALLS")
        for edge in calls_edges:
            assert edge.target in all_fqns, (
                f"CALLS edge targets unknown FQN: {edge.target}"
            )


# ===========================================================================
# 6. INHERITS edges
# ===========================================================================


class TestInheritsEdges:
    """INHERITS edges connect a class to its base class within the repo."""

    def test_inherits_from_repo_class(self, sample_repo: Path) -> None:
        """class User(BaseModel) -> INHERITS edge from User to BaseModel."""
        adg = parse_repo(sample_repo)
        assert _find_edge(adg, "app.models.user.User", "app.models.base.BaseModel", "INHERITS") is not None

    def test_no_inherits_from_external(self, sample_repo: Path) -> None:
        """INHERITS edges only target FQNs within the repo (no external base classes)."""
        adg = parse_repo(sample_repo)
        all_fqns = {str(n.fqn) for n in adg.nodes}
        inherits_edges = _find_edges(adg, "INHERITS")
        for edge in inherits_edges:
            assert edge.target in all_fqns, (
                f"INHERITS edge targets unknown FQN: {edge.target}"
            )


# ===========================================================================
# 7. Flask repo integration test
# ===========================================================================


class TestFlaskRepoIntegration:
    """Integration test: parse the flask sample repo and verify structure."""

    def test_flask_parse_produces_adg(self, flask_repo: Path) -> None:
        """parse_repo on flask repo returns a non-empty ADG."""
        adg = parse_repo(flask_repo)
        assert isinstance(adg, ADG)
        assert len(adg.nodes) > 0
        assert len(adg.edges) > 0

    def test_flask_module_nodes(self, flask_repo: Path) -> None:
        """Key module FQNs from the flask repo are present."""
        adg = parse_repo(flask_repo)
        assert _find_node(adg, "app") is not None
        assert _find_node(adg, "config") is not None
        assert _find_node(adg, "app.routes") is not None

    def test_flask_class_nodes(self, flask_repo: Path) -> None:
        """AuthMiddleware and User class nodes are extracted."""
        adg = parse_repo(flask_repo)
        assert _find_node(adg, "app.middleware.auth.AuthMiddleware") is not None
        assert _find_node(adg, "app.models.user.User") is not None

    def test_flask_method_nodes(self, flask_repo: Path) -> None:
        """Methods like _check_token and find are extracted."""
        adg = parse_repo(flask_repo)
        assert _find_node(adg, "app.middleware.auth.AuthMiddleware._check_token") is not None
        assert _find_node(adg, "app.models.user.User.find") is not None

    def test_flask_top_level_functions(self, flask_repo: Path) -> None:
        """Top-level functions like create_app, login, list_users_route."""
        adg = parse_repo(flask_repo)
        assert _find_node(adg, "app.create_app") is not None

    def test_flask_contains_edges(self, flask_repo: Path) -> None:
        """CONTAINS edges connect modules to their children."""
        adg = parse_repo(flask_repo)
        assert _find_edge(adg, "app", "app.create_app", "CONTAINS") is not None
        assert _find_edge(adg, "app.middleware.auth.AuthMiddleware", "app.middleware.auth.AuthMiddleware._check_token", "CONTAINS") is not None

    def test_flask_imports_edges(self, flask_repo: Path) -> None:
        """IMPORTS edges from the flask repo's import statements."""
        adg = parse_repo(flask_repo)
        imports = _find_edges(adg, "IMPORTS")
        assert len(imports) > 0

    def test_flask_no_duplicate_fqns(self, flask_repo: Path) -> None:
        """No duplicate FQNs in the node list."""
        adg = parse_repo(flask_repo)
        fqns = [n.fqn for n in adg.nodes]
        assert len(fqns) == len(set(fqns))


# ===========================================================================
# 8. Byte offsets on FQNNode
# ===========================================================================


class TestByteOffsets:
    """parse_repo populates start_byte and end_byte on FQNNode for content hashing."""

    def test_class_byte_offsets(self, sample_repo: Path) -> None:
        """Class nodes have start_byte and end_byte populated."""
        adg = parse_repo(sample_repo)
        user = _find_node(adg, "app.models.user.User")
        assert user is not None
        assert user.start_byte >= 0
        assert user.end_byte > user.start_byte

    def test_method_byte_offsets(self, sample_repo: Path) -> None:
        """Method nodes have start_byte and end_byte populated."""
        adg = parse_repo(sample_repo)
        find = _find_node(adg, "app.models.user.User.find")
        assert find is not None
        assert find.start_byte >= 0
        assert find.end_byte > find.start_byte

    def test_method_byte_offsets_within_class(self, sample_repo: Path) -> None:
        """Method byte offsets fall within the parent class byte offsets."""
        adg = parse_repo(sample_repo)
        user = _find_node(adg, "app.models.user.User")
        find = _find_node(adg, "app.models.user.User.find")
        assert user is not None
        assert find is not None
        assert find.start_byte >= user.start_byte
        assert find.end_byte <= user.end_byte

    def test_byte_offsets_slice_to_source(self, sample_repo: Path) -> None:
        """Slicing file source[start_byte:end_byte] returns the exact node text."""
        adg = parse_repo(sample_repo)
        user = _find_node(adg, "app.models.user.User")
        assert user is not None
        source = (sample_repo / user.file_path).read_bytes()
        node_text = source[user.start_byte:user.end_byte]
        assert b"class User" in node_text

    def test_function_byte_offsets(self, sample_repo: Path) -> None:
        """Top-level function nodes have start_byte and end_byte populated."""
        adg = parse_repo(sample_repo)
        get_user = _find_node(adg, "app.services.user_service.get_user")
        assert get_user is not None
        assert get_user.start_byte >= 0
        assert get_user.end_byte > get_user.start_byte

    def test_module_byte_offsets(self, sample_repo: Path) -> None:
        """Module nodes have start_byte and end_byte populated."""
        adg = parse_repo(sample_repo)
        modules = _find_nodes(adg, FQNKind.MODULE)
        assert len(modules) > 0
        for m in modules:
            assert m.start_byte >= 0
            assert m.end_byte >= m.start_byte

    def test_flask_class_byte_offsets(self, flask_repo: Path) -> None:
        """Flask repo class nodes have valid byte offsets."""
        adg = parse_repo(flask_repo)
        auth = _find_node(adg, "app.middleware.auth.AuthMiddleware")
        assert auth is not None
        assert auth.start_byte >= 0
        assert auth.end_byte > auth.start_byte


# ===========================================================================
# 9. Fail fast on syntax errors
# ===========================================================================


class TestSyntaxErrors:
    """parse_repo fails fast when Tree-sitter reports ERROR nodes."""

    def test_unclosed_parenthesis(self, tmp_path: Path) -> None:
        """A file with an unclosed parenthesis causes parse_repo to raise."""
        repo = tmp_path / "broken_repo"
        repo.mkdir()
        (repo / "app").mkdir()
        (repo / "app" / "__init__.py").write_text("")
        (repo / "app" / "broken.py").write_text("def foo(:\n    pass\n")
        with pytest.raises(Exception):
            parse_repo(repo)

    def test_missing_colon(self, tmp_path: Path) -> None:
        """A class definition missing a colon causes parse_repo to raise."""
        repo = tmp_path / "broken_repo"
        repo.mkdir()
        (repo / "app").mkdir()
        (repo / "app" / "__init__.py").write_text("")
        (repo / "app" / "broken.py").write_text("class User\n    pass\n")
        with pytest.raises(Exception):
            parse_repo(repo)

    def test_missing_parenthesis_in_params(self, tmp_path: Path) -> None:
        """Mismatched parentheses in a function definition cause parse_repo to raise."""
        repo = tmp_path / "broken_repo"
        repo.mkdir()
        (repo / "app").mkdir()
        (repo / "app" / "__init__.py").write_text("")
        (repo / "app" / "broken.py").write_text("def foo(a, b:\n    pass\n")
        with pytest.raises(Exception):
            parse_repo(repo)

    def test_valid_repo_still_passes(self, sample_repo: Path) -> None:
        """A repo with no syntax errors does not raise."""
        adg = parse_repo(sample_repo)
        assert len(adg.nodes) > 0