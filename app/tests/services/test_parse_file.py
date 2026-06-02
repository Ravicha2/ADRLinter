"""Tests for parse_file: extract FQN nodes and CONTAINS edges from a single Python file.

Public interface under test:
    parse_file(source: bytes, module_fqn: FQN, rel_path: str) -> tuple[list[FQNNode], list[Edge]]

Each test class covers one behavioral aspect of parse_file.
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import Edge, FQNKind, FQNNode
from services.treesitter import parse_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_node(nodes: list[FQNNode], fqn: str) -> FQNNode | None:
    """Find FQN node by fully qualified name."""
    target = FQN.from_dotted(fqn)
    for n in nodes:
        if n.fqn == target:
            return n
    return None


def _find_nodes(nodes: list[FQNNode], kind: FQNKind) -> list[FQNNode]:
    """Find all FQN nodes of a given kind."""
    return [n for n in nodes if n.kind == kind]


# ===========================================================================
# 1. Class extraction
# ===========================================================================


class TestClassExtraction:
    """parse_file extracts class definitions as FQN nodes."""

    def test_class_node(self) -> None:
        """A class definition produces a node with kind=CLASS."""
        source = b"class User:\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        user = _find_node(nodes, "app.models.user.User")
        assert user is not None
        assert user.kind == FQNKind.CLASS
        assert user.file_path == "app/models/user.py"

    def test_class_fqn_includes_module(self) -> None:
        """Class FQN is module_fqn + class name."""
        source = b"class AuthMiddleware:\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.middleware.auth"), rel_path="app/middleware/auth.py")
        assert _find_node(nodes, "app.middleware.auth.AuthMiddleware") is not None

    def test_multiple_classes(self) -> None:
        """Multiple classes in one file each produce a node."""
        source = b"class User:\n    pass\n\nclass Admin:\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models"), rel_path="app/models.py")
        assert _find_node(nodes, "app.models.User") is not None
        assert _find_node(nodes, "app.models.Admin") is not None

    def test_class_line_range(self) -> None:
        """Class node has line_start and line_end spanning the definition."""
        source = b"class User:\n    def find(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        user = _find_node(nodes, "app.models.user.User")
        assert user is not None
        assert user.line_start >= 0
        assert user.line_end > user.line_start


# ===========================================================================
# 2. Method extraction
# ===========================================================================


class TestMethodExtraction:
    """parse_file extracts methods (functions inside classes) with kind=METHOD."""

    def test_method_node(self) -> None:
        """A method inside a class produces a node with kind=METHOD."""
        source = b"class User:\n    def find(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        find = _find_node(nodes, "app.models.user.User.find")
        assert find is not None
        assert find.kind == FQNKind.METHOD

    def test_multiple_methods(self) -> None:
        """Multiple methods in a class each produce a node."""
        source = b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        assert _find_node(nodes, "app.models.user.User.find") is not None
        assert _find_node(nodes, "app.models.user.User.all") is not None

    def test_method_line_range_within_class(self) -> None:
        """Method line ranges fall within the parent class range."""
        source = b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        user = _find_node(nodes, "app.models.user.User")
        find = _find_node(nodes, "app.models.user.User.find")
        assert user is not None
        assert find is not None
        assert find.line_start >= user.line_start
        assert find.line_end <= user.line_end

    def test_staticmethod(self) -> None:
        """@staticmethod decorated methods are still extracted as methods."""
        source = b"class User:\n    @staticmethod\n    def find(user_id):\n        return {}\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        assert _find_node(nodes, "app.models.user.User.find") is not None
        assert _find_node(nodes, "app.models.user.User.find").kind == FQNKind.METHOD


# ===========================================================================
# 3. Top-level function extraction
# ===========================================================================


class TestFunctionExtraction:
    """parse_file extracts top-level functions with kind=FUNCTION."""

    def test_function_node(self) -> None:
        """A top-level function produces a node with kind=FUNCTION."""
        source = b"def get_user(user_id):\n    return {}\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.services.user_service"), rel_path="app/services/user_service.py")
        func = _find_node(nodes, "app.services.user_service.get_user")
        assert func is not None
        assert func.kind == FQNKind.FUNCTION

    def test_function_is_not_method(self) -> None:
        """Top-level functions have kind=FUNCTION, not METHOD."""
        source = b"def helper():\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.utils"), rel_path="app/utils.py")
        assert _find_node(nodes, "app.utils.helper").kind == FQNKind.FUNCTION

    def test_multiple_functions(self) -> None:
        """Multiple top-level functions each produce a node."""
        source = b"def create():\n    pass\n\ndef delete():\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.ops"), rel_path="app/ops.py")
        assert _find_node(nodes, "app.ops.create") is not None
        assert _find_node(nodes, "app.ops.delete") is not None


# ===========================================================================
# 4. Nested class handling
# ===========================================================================


class TestNestedClass:
    """parse_file handles nested classes correctly."""

    def test_nested_class(self) -> None:
        """A class defined inside another class produces a nested FQN."""
        source = b"class User:\n    class Meta:\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        assert _find_node(nodes, "app.models.user.User.Meta") is not None

    def test_nested_class_method(self) -> None:
        """A method inside a nested class has the full nested FQN."""
        source = b"class User:\n    class Meta:\n        def ordering(self):\n            pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        assert _find_node(nodes, "app.models.user.User.Meta.ordering") is not None
        assert _find_node(nodes, "app.models.user.User.Meta.ordering").kind == FQNKind.METHOD


# ===========================================================================
# 5. Fail fast on syntax errors
# ===========================================================================


class TestSyntaxErrors:
    """parse_file fails fast when Tree-sitter reports ERROR nodes."""

    def test_unclosed_parenthesis(self) -> None:
        """A file with an unclosed parenthesis raises an error."""
        source = b"def foo(:\n    pass\n"
        with pytest.raises(Exception):
            parse_file(source, module_fqn=FQN.from_dotted("app.broken"), rel_path="app/broken.py")

    def test_missing_parenthesis_in_params(self) -> None:
        """A function with mismatched parentheses raises an error."""
        source = b"def foo(a, b:\n    pass\n"
        with pytest.raises(Exception):
            parse_file(source, module_fqn=FQN.from_dotted("app.broken"), rel_path="app/broken.py")

    def test_missing_colon(self) -> None:
        """A class definition missing a colon raises an error."""
        source = b"class User\n    pass\n"
        with pytest.raises(Exception):
            parse_file(source, module_fqn=FQN.from_dotted("app.broken"), rel_path="app/broken.py")


# ===========================================================================
# 6. Byte offsets for content hashing
# ===========================================================================


class TestByteOffsets:
    """parse_file returns FQN nodes with start_byte and end_byte for content hashing."""

    def test_class_byte_offsets(self) -> None:
        """Class node has start_byte and end_byte populated."""
        source = b"class User:\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        user = _find_node(nodes, "app.models.user.User")
        assert user is not None
        assert user.start_byte >= 0
        assert user.end_byte > user.start_byte

    def test_method_byte_offsets(self) -> None:
        """Method node has start_byte and end_byte populated."""
        source = b"class User:\n    def find(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        find = _find_node(nodes, "app.models.user.User.find")
        assert find is not None
        assert find.start_byte >= 0
        assert find.end_byte > find.start_byte

    def test_byte_offsets_slice_to_source(self) -> None:
        """Slicing source[start_byte:end_byte] returns the exact node text."""
        source = b"class User:\n    def find(self):\n        return {}\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        find = _find_node(nodes, "app.models.user.User.find")
        assert find is not None
        node_text = source[find.start_byte:find.end_byte]
        assert b"def find" in node_text
        assert b"return" in node_text

    def test_method_byte_offsets_within_class(self) -> None:
        """Method byte offsets fall within the parent class byte offsets."""
        source = b"class User:\n    def find(self):\n        pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.models.user"), rel_path="app/models/user.py")
        user = _find_node(nodes, "app.models.user.User")
        find = _find_node(nodes, "app.models.user.User.find")
        assert user is not None
        assert find is not None
        assert find.start_byte >= user.start_byte
        assert find.end_byte <= user.end_byte


# ===========================================================================
# 7. No duplicate FQNs
# ===========================================================================


class TestNoDuplicates:
    """parse_file does not produce duplicate FQN nodes."""

    def test_no_duplicate_fqns(self) -> None:
        """Each FQN appears at most once in the node list."""
        source = b"class User:\n    def find(self):\n        pass\n\ndef helper():\n    pass\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.mixed"), rel_path="app/mixed.py")
        fqns = [n.fqn for n in nodes]
        assert len(fqns) == len(set(fqns))


# ===========================================================================
# 8. Empty file
# ===========================================================================


class TestEmptyFile:
    """parse_file handles empty or trivial files."""

    def test_empty_file(self) -> None:
        """An empty file produces no FQN nodes."""
        source = b""
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.empty"), rel_path="app/empty.py")
        assert nodes == []

    def test_only_comments(self) -> None:
        """A file with only comments produces no FQN nodes."""
        source = b"# This is a comment\n# Another comment\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.comments"), rel_path="app/comments.py")
        assert nodes == []

    def test_only_imports(self) -> None:
        """A file with only import statements produces no FQN nodes (no definitions)."""
        source = b"import os\nfrom sys import path\n"
        nodes, _ = parse_file(source, module_fqn=FQN.from_dotted("app.imports_only"), rel_path="app/imports_only.py")
        assert len(nodes) == 0