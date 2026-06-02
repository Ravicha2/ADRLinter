"""Boundary tests for the FQN frozen dataclass.

Covers construction, interpretation, validation, equality, hashing, and
immutability.  The FQN type centralizes all FQN creation and manipulation
that was previously scattered across treesitter.py and diff_processor.py.
"""

from __future__ import annotations

import pytest

from services.fqn import FQN


# ===========================================================================
# 1. Construction: from_dotted
# ===========================================================================


class TestFromDotted:
    """FQN.from_dotted creates an FQN from a dotted string."""

    def test_simple(self) -> None:
        assert str(FQN.from_dotted("app")) == "app"

    def test_two_segments(self) -> None:
        assert str(FQN.from_dotted("app.config")) == "app.config"

    def test_three_segments(self) -> None:
        assert str(FQN.from_dotted("app.models.user")) == "app.models.user"

    def test_deeply_nested(self) -> None:
        assert str(FQN.from_dotted("app.models.user.User.find")) == "app.models.user.User.find"


# ===========================================================================
# 2. Construction: from_path
# ===========================================================================


class TestFromPath:
    """FQN.from_path converts a relative file path to a module FQN."""

    def test_regular_module(self) -> None:
        """app/services/user_service.py -> app.services.user_service"""
        assert str(FQN.from_path("app/services/user_service.py")) == "app.services.user_service"

    def test_init_py(self) -> None:
        """app/models/__init__.py -> app.models"""
        assert str(FQN.from_path("app/models/__init__.py")) == "app.models"

    def test_top_level_init(self) -> None:
        """app/__init__.py -> app"""
        assert str(FQN.from_path("app/__init__.py")) == "app"

    def test_top_level_module(self) -> None:
        """app/config.py -> app.config"""
        assert str(FQN.from_path("app/config.py")) == "app.config"

    def test_root_module(self) -> None:
        """config.py -> config"""
        assert str(FQN.from_path("config.py")) == "config"

    def test_backslash_path(self) -> None:
        """Backslash paths are normalized to dots."""
        assert str(FQN.from_path("app\\models\\user.py")) == "app.models.user"


# ===========================================================================
# 3. Construction: child
# ===========================================================================


class TestChild:
    """FQN.child appends a name segment to produce a descendant FQN."""

    def test_class_from_module(self) -> None:
        module = FQN.from_dotted("app.models.user")
        cls = module.child("User")
        assert str(cls) == "app.models.user.User"

    def test_method_from_class(self) -> None:
        cls = FQN.from_dotted("app.models.user.User")
        method = cls.child("find")
        assert str(method) == "app.models.user.User.find"

    def test_child_of_single_segment(self) -> None:
        root = FQN.from_dotted("app")
        child = root.child("config")
        assert str(child) == "app.config"

    def test_child_rejects_empty(self) -> None:
        root = FQN.from_dotted("app")
        with pytest.raises(ValueError):
            root.child("")


# ===========================================================================
# 4. Interpretation: parent, name, parts
# ===========================================================================


class TestInterpretation:
    """FQN.parent, .name, and .parts decompose an FQN."""

    def test_parent_of_deep(self) -> None:
        fqn = FQN.from_dotted("app.models.user.User.find")
        assert fqn.parent is not None
        assert str(fqn.parent) == "app.models.user.User"

    def test_parent_of_two_segments(self) -> None:
        fqn = FQN.from_dotted("app.config")
        assert fqn.parent is not None
        assert str(fqn.parent) == "app"

    def test_parent_of_root_is_none(self) -> None:
        fqn = FQN.from_dotted("app")
        assert fqn.parent is None

    def test_name_returns_last_segment(self) -> None:
        assert FQN.from_dotted("app.models.user.User.find").name == "find"

    def test_name_of_root(self) -> None:
        assert FQN.from_dotted("app").name == "app"

    def test_parts_returns_tuple(self) -> None:
        fqn = FQN.from_dotted("app.models.user")
        assert fqn.parts == ("app", "models", "user")

    def test_parts_of_root(self) -> None:
        assert FQN.from_dotted("app").parts == ("app",)


# ===========================================================================
# 5. Validation
# ===========================================================================


class TestValidation:
    """FQN.from_dotted rejects malformed dotted strings."""

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            FQN.from_dotted("")

    def test_leading_dot(self) -> None:
        with pytest.raises(ValueError):
            FQN.from_dotted(".app.models")

    def test_trailing_dot(self) -> None:
        with pytest.raises(ValueError):
            FQN.from_dotted("app.models.")

    def test_double_dot(self) -> None:
        with pytest.raises(ValueError):
            FQN.from_dotted("app..models")

    def test_only_dots(self) -> None:
        with pytest.raises(ValueError):
            FQN.from_dotted("...")


# ===========================================================================
# 5b. from_dotted_safe
# ===========================================================================


class TestFromDottedSafe:
    """FQN.from_dotted_safe returns None instead of raising."""

    def test_valid_returns_fqn(self) -> None:
        assert FQN.from_dotted_safe("app.models") == FQN.from_dotted("app.models")

    def test_empty_returns_none(self) -> None:
        assert FQN.from_dotted_safe("") is None

    def test_double_dot_returns_none(self) -> None:
        assert FQN.from_dotted_safe("app..models") is None


# ===========================================================================
# 6. Equality and hashing
# ===========================================================================


class TestEqualityAndHashing:
    """FQNs with the same segments are equal and hash the same."""

    def test_equal_fqns(self) -> None:
        a = FQN.from_dotted("app.models.user")
        b = FQN.from_dotted("app.models.user")
        assert a == b

    def test_unequal_fqns(self) -> None:
        a = FQN.from_dotted("app.models.user")
        b = FQN.from_dotted("app.models.base")
        assert a != b

    def test_not_equal_to_string(self) -> None:
        fqn = FQN.from_dotted("app.models.user")
        assert fqn != "app.models.user"

    def test_hash_equal_fqns(self) -> None:
        a = FQN.from_dotted("app.models.user")
        b = FQN.from_dotted("app.models.user")
        assert hash(a) == hash(b)

    def test_fqn_in_set(self) -> None:
        """FQNs can be used in sets and as dict keys."""
        a = FQN.from_dotted("app.models.user")
        b = FQN.from_dotted("app.models.user")
        c = FQN.from_dotted("app.models.base")
        s = {a, b, c}
        assert len(s) == 2

    def test_from_path_and_from_dotted_equal(self) -> None:
        """from_path and from_dotted produce the same FQN for equivalent inputs."""
        assert FQN.from_path("app/models/user.py") == FQN.from_dotted("app.models.user")

    def test_child_equals_from_dotted(self) -> None:
        """child produces the same FQN as from_dotted for equivalent paths."""
        parent = FQN.from_dotted("app.models.user")
        assert parent.child("User") == FQN.from_dotted("app.models.user.User")


# ===========================================================================
# 7. Immutability
# ===========================================================================


class TestImmutability:
    """FQN is frozen; attributes cannot be reassigned."""

    def test_cannot_reassign_parts(self) -> None:
        fqn = FQN.from_dotted("app.models")
        with pytest.raises(AttributeError):
            fqn._parts = ("other",)  # type: ignore[mutable-attribute]

    def test_cannot_add_attribute(self) -> None:
        fqn = FQN.from_dotted("app.models")
        with pytest.raises((AttributeError, TypeError)):
            fqn.extra = True  # type: ignore[attr-defined]