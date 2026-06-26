"""Tests for process_diff: identify changed FQNs from a CommitDiff.

Public interface under test:
    process_diff(commit_diff: CommitDiff) -> DiffResult

Each test class covers one behavioral aspect of the Diff Processor,
using in-memory CommitDiff fixtures (no git dependency).
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import ChangedFQN, CommitDiff, DiffResult, FileChange, FQNKind
from services.cpt import process_diff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sample Python file contents used across tests

USER_SERVICE_OLD = b"from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n"

USER_SERVICE_NEW = b"from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n\ndef create_user(data):\n    return User.create(data)\n"

USER_SERVICE_MODIFIED = (
    b"from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id, active=True)\n"
)

USER_MODEL_OLD = b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"

USER_MODEL_NEW = b"class User:\n    def find(self):\n        pass\n"

CONFIG_PY = b"DEBUG = True\nSECRET_KEY = 'dev'\n"

README_MD = b"# My Project\n\nThis is a readme.\n"


def _find_changed(result: DiffResult, fqn: str) -> ChangedFQN | None:
    """Find a ChangedFQN by its FQN string."""
    target = FQN.from_dotted(fqn)
    for c in result.changed_fqns:
        if c.fqn == target:
            return c
    return None


def _find_changed_by_type(result: DiffResult, change_type: str) -> list[ChangedFQN]:
    """Find all ChangedFQNs of a given change_type."""
    return [c for c in result.changed_fqns if c.change_type == change_type]


def _find_file_change(result: DiffResult, path: str) -> FileChange | None:
    """Find a FileChange by path."""
    for f in result.changed_files:
        if f.path == path:
            return f
    return None


# ===========================================================================
# 1. Added file
# ===========================================================================


class TestAddedFile:
    """All FQNs in an added file have change_type='added'."""

    def test_added_file_all_fqns_added(self) -> None:
        """An added .py file reports all its FQNs as 'added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="added")],
            file_contents={"app/services/user_service.py": USER_SERVICE_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        added = _find_changed_by_type(result, "added")
        assert len(added) > 0
        assert _find_changed(result, "app.services.user_service.get_user") is not None
        assert _find_changed(result, "app.services.user_service.get_user").change_type == "added"

    def test_added_class(self) -> None:
        """A class in an added file is reported as 'added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        user_class = _find_changed(result, "app.models.user.User")
        assert user_class is not None
        assert user_class.change_type == "added"

    def test_added_methods(self) -> None:
        """Methods inside a class in an added file are reported as 'added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.models.user.User.find").change_type == "added"
        assert _find_changed(result, "app.models.user.User.all").change_type == "added"


# ===========================================================================
# 2. Deleted file
# ===========================================================================


class TestDeletedFile:
    """All FQNs in a deleted file have change_type='deleted'."""

    def test_deleted_file_all_fqns_deleted(self) -> None:
        """A deleted .py file reports all its FQNs as 'deleted'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="deleted")],
            file_contents={},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        deleted = _find_changed_by_type(result, "deleted")
        assert len(deleted) > 0
        assert _find_changed(result, "app.services.user_service.get_user").change_type == "deleted"

    def test_deleted_class(self) -> None:
        """A class in a deleted file is reported as 'deleted'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="deleted")],
            file_contents={},
            parent_contents={"app/models/user.py": USER_MODEL_OLD},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.models.user.User").change_type == "deleted"

    def test_deleted_methods(self) -> None:
        """Methods in a deleted file are reported as 'deleted'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="deleted")],
            file_contents={},
            parent_contents={"app/models/user.py": USER_MODEL_OLD},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.models.user.User.find").change_type == "deleted"
        assert _find_changed(result, "app.models.user.User.all").change_type == "deleted"


# ===========================================================================
# 3. Modified file: new function added
# ===========================================================================


class TestModifiedFileNewFunction:
    """A new function in a modified file is reported as 'added'."""

    def test_new_function_added(self) -> None:
        """A new top-level function in a modified file has change_type='added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="modified")],
            file_contents={"app/services/user_service.py": USER_SERVICE_NEW},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.services.user_service.create_user").change_type == "added"

    def test_existing_function_unchanged(self) -> None:
        """An unchanged function in a modified file is not reported."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="modified")],
            file_contents={"app/services/user_service.py": USER_SERVICE_NEW},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        get_user = _find_changed(result, "app.services.user_service.get_user")
        assert get_user is None


# ===========================================================================
# 4. Modified file: function removed
# ===========================================================================


class TestModifiedFileFunctionRemoved:
    """A removed function in a modified file is reported as 'deleted'."""

    def test_method_removed(self) -> None:
        """A method removed from a class has change_type='deleted'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="modified")],
            file_contents={"app/models/user.py": USER_MODEL_NEW},
            parent_contents={"app/models/user.py": USER_MODEL_OLD},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.models.user.User.all").change_type == "deleted"


# ===========================================================================
# 5. Modified file: function body changed (content hash)
# ===========================================================================


class TestModifiedFileBodyChanged:
    """A function whose body changed is reported as 'modified'."""

    def test_function_body_changed(self) -> None:
        """A function with a different body has change_type='modified'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="modified")],
            file_contents={"app/services/user_service.py": USER_SERVICE_MODIFIED},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        get_user = _find_changed(result, "app.services.user_service.get_user")
        assert get_user is not None
        assert get_user.change_type == "modified"

    def test_unchanged_function_not_reported(self) -> None:
        """A function with identical content in old and new is not in output."""
        source_unchanged = b"def helper():\n    pass\n"
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/utils.py", status="modified")],
            file_contents={"app/utils.py": source_unchanged},
            parent_contents={"app/utils.py": source_unchanged},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.utils.helper") is None

    def test_whitespace_only_change_is_modified(self) -> None:
        """A whitespace-only change in a function body changes the content hash.
        Phase 1 accepts this as 'modified'. Upgrade to changed_ranges filters it."""
        source_old = b"def greet():\n    return 'hello'\n"
        source_new = b"def greet():\n    return  'hello'\n"  # extra space
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/greet.py", status="modified")],
            file_contents={"app/greet.py": source_new},
            parent_contents={"app/greet.py": source_old},
        )
        result = process_diff(commit_diff)
        greet = _find_changed(result, "app.greet.greet")
        assert greet is not None
        assert greet.change_type == "modified"


# ===========================================================================
# 6. Enclosing scope
# ===========================================================================


class TestEnclosingScope:
    """ChangedFQN carries enclosing_class and enclosing_module."""

    def test_method_has_enclosing_class(self) -> None:
        """A method's enclosing_class is the parent class FQN."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        find_method = _find_changed(result, "app.models.user.User.find")
        assert find_method is not None
        assert find_method.enclosing_class == FQN.from_dotted("app.models.user.User")
        assert find_method.enclosing_module == FQN.from_dotted("app.models.user")

    def test_class_has_no_enclosing_class(self) -> None:
        """A top-level class has no enclosing_class (None)."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        user_class = _find_changed(result, "app.models.user.User")
        assert user_class is not None
        assert user_class.enclosing_class is None
        assert user_class.enclosing_module == FQN.from_dotted("app.models.user")

    def test_top_level_function_has_no_enclosing_class(self) -> None:
        """A top-level function has no enclosing_class."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="added")],
            file_contents={"app/services/user_service.py": USER_SERVICE_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        func = _find_changed(result, "app.services.user_service.get_user")
        assert func is not None
        assert func.enclosing_class is None
        assert func.enclosing_module == FQN.from_dotted("app.services.user_service")


# ===========================================================================
# 7. Non-.py files
# ===========================================================================


class TestNonPythonFiles:
    """Non-.py files appear in changed_files but produce no changed_fqns."""

    def test_markdown_in_changed_files(self) -> None:
        """A .md file appears in changed_files but not in changed_fqns."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="README.md", status="modified")],
            file_contents={"README.md": README_MD},
            parent_contents={"README.md": b"# Old readme\n"},
        )
        result = process_diff(commit_diff)
        assert _find_file_change(result, "README.md") is not None
        assert result.changed_fqns == []

    def test_yaml_in_changed_files(self) -> None:
        """A .yaml file appears in changed_files but produces no FQNs."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="config.yaml", status="added")],
            file_contents={"config.yaml": b"key: value\n"},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        assert _find_file_change(result, "config.yaml") is not None
        assert result.changed_fqns == []

    def test_mixed_py_and_non_py(self) -> None:
        """A commit with both .py and non-.py files: .py with no defs emits a module-level FQN, non-.py does not."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="app/config.py", status="added"),
                FileChange(path="README.md", status="modified"),
            ],
            file_contents={"app/config.py": CONFIG_PY, "README.md": README_MD},
            parent_contents={"README.md": b"old\n"},
        )
        result = process_diff(commit_diff)
        # config.py has no class/def, but a module-level FQN is emitted so BFS still has a start.
        config_mod = _find_changed(result, "app.config")
        assert config_mod is not None
        assert config_mod.change_type == "added"
        assert config_mod.enclosing_module == FQN.from_dotted("app.config")
        # non-.py file still produces no FQN
        assert _find_changed(result, "README") is None
        assert len(result.changed_files) == 2


# ===========================================================================
# 7b. Module-level fallback (no class/def in file)
# ===========================================================================


class TestModuleLevelFallback:
    """When a .py file changes bytes but has no class/def, emit a module-level ChangedFQN."""

    def test_modified_module_level_only(self) -> None:
        """A settings.py with only module-level assignments: modified bytes -> module FQN."""
        old = b"DEBUG = True\nSECRET_KEY = 'dev'\n"
        new = b"DEBUG = False\nSECRET_KEY = 'prod'\n"
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/settings.py", status="modified")],
            file_contents={"app/settings.py": new},
            parent_contents={"app/settings.py": old},
        )
        result = process_diff(commit_diff)
        mod = _find_changed(result, "app.settings")
        assert mod is not None
        assert mod.change_type == "modified"
        assert mod.enclosing_module == FQN.from_dotted("app.settings")

    def test_added_module_level_only(self) -> None:
        """A new .py file with only module-level assignments emits an added module FQN."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/settings.py", status="added")],
            file_contents={"app/settings.py": CONFIG_PY},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        mod = _find_changed(result, "app.settings")
        assert mod is not None
        assert mod.change_type == "added"

    def test_deleted_module_level_only(self) -> None:
        """A deleted .py file with only module-level assignments emits a deleted module FQN."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/settings.py", status="deleted")],
            file_contents={},
            parent_contents={"app/settings.py": CONFIG_PY},
        )
        result = process_diff(commit_diff)
        mod = _find_changed(result, "app.settings")
        assert mod is not None
        assert mod.change_type == "deleted"

    def test_unchanged_bytes_no_module_fqn(self) -> None:
        """A modified .py file whose bytes are identical emits no module FQN."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/settings.py", status="modified")],
            file_contents={"app/settings.py": CONFIG_PY},
            parent_contents={"app/settings.py": CONFIG_PY},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.settings") is None

    def test_def_present_no_module_fqn(self) -> None:
        """A modified .py file with a changed def emits only the def FQN, not the module."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/services/user_service.py", status="modified")],
            file_contents={"app/services/user_service.py": USER_SERVICE_MODIFIED},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        assert _find_changed(result, "app.services.user_service") is None
        assert _find_changed(result, "app.services.user_service.get_user") is not None


# ===========================================================================
# 8. First commit (no parent)
# ===========================================================================


class TestFirstCommit:
    """First commit has no parent; all FQNs are 'added'."""

    def test_first_commit_all_added(self) -> None:
        """With parent_sha=None and empty parent_contents, all FQNs are 'added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha=None,
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        all_changes = result.changed_fqns
        assert len(all_changes) > 0
        for c in all_changes:
            assert c.change_type == "added"

    def test_first_commit_commit_sha_preserved(self) -> None:
        """The result carries the original commit_sha."""
        commit_diff = CommitDiff(
            commit_sha="first123",
            parent_sha=None,
            changed_files=[FileChange(path="app/models/user.py", status="added")],
            file_contents={"app/models/user.py": USER_MODEL_OLD},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        assert result.commit_sha == "first123"


# ===========================================================================
# 9. Renamed file
# ===========================================================================


class TestRenamedFile:
    """Renamed files are treated as delete + add in Phase 1."""

    def test_renamed_file_old_fqns_deleted(self) -> None:
        """FQNs from the old path are reported as 'deleted'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="app/services/auth_service.py", status="renamed", old_path="app/services/user_service.py")
            ],
            file_contents={"app/services/auth_service.py": USER_SERVICE_OLD},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        deleted_old = [c for c in result.changed_fqns if c.change_type == "deleted" and "user_service" in str(c.fqn)]
        assert len(deleted_old) > 0

    def test_renamed_file_new_fqns_added(self) -> None:
        """FQNs from the new path are reported as 'added'."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="app/services/auth_service.py", status="renamed", old_path="app/services/user_service.py")
            ],
            file_contents={"app/services/auth_service.py": USER_SERVICE_OLD},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        added_new = [c for c in result.changed_fqns if c.change_type == "added" and "auth_service" in str(c.fqn)]
        assert len(added_new) > 0

    def test_renamed_file_both_in_output(self) -> None:
        """Both old (deleted) and new (added) FQNs appear in output."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="app/services/auth_service.py", status="renamed", old_path="app/services/user_service.py")
            ],
            file_contents={"app/services/auth_service.py": USER_SERVICE_OLD},
            parent_contents={"app/services/user_service.py": USER_SERVICE_OLD},
        )
        result = process_diff(commit_diff)
        total = len(result.changed_fqns)
        deleted = len(_find_changed_by_type(result, "deleted"))
        added = len(_find_changed_by_type(result, "added"))
        assert total == deleted + added  # no "modified" when file is renamed


# ===========================================================================
# 10. Fail fast on syntax errors
# ===========================================================================


class TestSyntaxErrorsInDiff:
    """process_diff fails fast when a changed file has syntax errors."""

    def test_syntax_error_in_new_file(self) -> None:
        """A syntax error in a new file causes process_diff to raise."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/broken.py", status="added")],
            file_contents={"app/broken.py": b"def foo(:\n    pass\n"},
            parent_contents={},
        )
        with pytest.raises(Exception):
            process_diff(commit_diff)

    def test_syntax_error_in_modified_file(self) -> None:
        """A syntax error in a modified file causes process_diff to raise."""
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[FileChange(path="app/broken.py", status="modified")],
            file_contents={"app/broken.py": b"class User\n    pass\n"},
            parent_contents={"app/broken.py": USER_MODEL_OLD},
        )
        with pytest.raises(Exception):
            process_diff(commit_diff)


# ===========================================================================
# 11. changed_files passes through
# ===========================================================================


class TestChangedFilesPassthrough:
    """changed_files in DiffResult mirrors the input CommitDiff."""

    def test_changed_files_preserved(self) -> None:
        """All FileChange entries from the input appear in the output."""
        file_changes = [
            FileChange(path="app/models/user.py", status="added"),
            FileChange(path="README.md", status="modified"),
        ]
        commit_diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=file_changes,
            file_contents={"app/models/user.py": USER_MODEL_OLD, "README.md": README_MD},
            parent_contents={"README.md": b"old\n"},
        )
        result = process_diff(commit_diff)
        assert len(result.changed_files) == 2
        assert _find_file_change(result, "app/models/user.py") is not None
        assert _find_file_change(result, "README.md") is not None

    def test_commit_sha_preserved(self) -> None:
        """commit_sha in DiffResult matches the input."""
        commit_diff = CommitDiff(
            commit_sha="sha789",
            parent_sha="sha788",
            changed_files=[FileChange(path="app/config.py", status="added")],
            file_contents={"app/config.py": CONFIG_PY},
            parent_contents={},
        )
        result = process_diff(commit_diff)
        assert result.commit_sha == "sha789"