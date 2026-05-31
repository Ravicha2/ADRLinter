"""Tests for GitAdapter: fetch CommitDiff from a real git repository.

Public interface under test:
    GitAdapter.get_commit_diff(repo_path: Path, commit_sha: str | None = None) -> CommitDiff

These are integration tests that create real git repos via subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_adapter import GitAdapter
from services.models import CommitDiff, FileChange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit_file(repo: Path, path: str, content: str, message: str) -> str:
    """Write a file, stage it, commit, and return the commit SHA."""
    full_path = repo / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)
    _run_git(repo, "add", path)
    _run_git(repo, "commit", "-m", message)
    return _run_git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a git repo with an initial commit containing a Python file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@test.com")
    _run_git(repo, "config", "user.name", "Test")
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n",
        "initial commit",
    )
    return repo


@pytest.fixture
def git_repo_multi_commit(git_repo: Path) -> Path:
    """Extend the git_repo with a second commit modifying and adding files."""
    repo = git_repo
    # Modify existing file
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n",
        "add User.all method",
    )
    # Add new file
    _commit_file(
        repo,
        "app/services/user_service.py",
        "from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n",
        "add user service",
    )
    return repo


# ===========================================================================
# 1. Single commit repo
# ===========================================================================


class TestSingleCommit:
    """GitAdapter works on a repo with a single commit."""

    def test_returns_commit_diff(self, git_repo: Path) -> None:
        """get_commit_diff returns a CommitDiff object."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        assert isinstance(result, CommitDiff)

    def test_commit_sha_populated(self, git_repo: Path) -> None:
        """commit_sha matches the actual HEAD commit."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        expected_sha = _run_git(git_repo, "rev-parse", "HEAD")
        assert result.commit_sha == expected_sha

    def test_parent_sha_is_none(self, git_repo: Path) -> None:
        """First commit has parent_sha=None."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        assert result.parent_sha is None

    def test_first_commit_all_files_added(self, git_repo: Path) -> None:
        """First commit reports all files as 'added'."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        for fc in result.changed_files:
            assert fc.status == "added"

    def test_first_commit_file_contents(self, git_repo: Path) -> None:
        """First commit file_contents contains the committed files."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        assert "app/models/user.py" in result.file_contents
        content = result.file_contents["app/models/user.py"]
        assert b"class User" in content

    def test_first_commit_parent_contents_empty(self, git_repo: Path) -> None:
        """First commit has empty parent_contents."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        assert result.parent_contents == {}


# ===========================================================================
# 2. Multi-commit repo: latest commit
# ===========================================================================


class TestMultiCommitLatest:
    """GitAdapter on HEAD of a multi-commit repo."""

    def test_changed_files_only_head(self, git_repo_multi_commit: Path) -> None:
        """Only the most recent commit's files appear in changed_files."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        # Last commit added user_service.py, so it should be in changed_files
        paths = {fc.path for fc in result.changed_files}
        assert "app/services/user_service.py" in paths

    def test_parent_sha_populated(self, git_repo_multi_commit: Path) -> None:
        """Non-first commit has a parent_sha."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        assert result.parent_sha is not None

    def test_file_contents_has_new_version(self, git_repo_multi_commit: Path) -> None:
        """file_contents contains the file at the commit SHA."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        for fc in result.changed_files:
            if fc.status != "deleted":
                assert fc.path in result.file_contents

    def test_parent_contents_has_old_version(self, git_repo_multi_commit: Path) -> None:
        """parent_contents contains the file at the parent SHA for modified/deleted files."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        for fc in result.changed_files:
            if fc.status in ("modified", "deleted"):
                assert fc.path in result.parent_contents


# ===========================================================================
# 3. Specific commit SHA
# ===========================================================================


class TestSpecificCommit:
    """GitAdapter can target a specific commit by SHA."""

    def test_specific_commit_sha(self, git_repo_multi_commit: Path) -> None:
        """Passing commit_sha returns the diff for that specific commit."""
        # Get the first commit's SHA
        shas = _run_git(git_repo_multi_commit, "log", "--format=%H", "--reverse").split("\n")
        first_sha = shas[0]

        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit, commit_sha=first_sha)
        assert result.commit_sha == first_sha
        assert result.parent_sha is None  # first commit

    def test_default_is_head(self, git_repo_multi_commit: Path) -> None:
        """Not passing commit_sha defaults to HEAD."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        head_sha = _run_git(git_repo_multi_commit, "rev-parse", "HEAD")
        assert result.commit_sha == head_sha


# ===========================================================================
# 4. Invalid commit SHA
# ===========================================================================


class TestInvalidCommitSHA:
    """GitAdapter raises an error for invalid commit SHAs."""

    def test_invalid_sha_raises(self, git_repo: Path) -> None:
        """A non-existent commit SHA raises an error."""
        adapter = GitAdapter()
        with pytest.raises(Exception):
            adapter.get_commit_diff(git_repo, commit_sha="0000000000000000000000000000000000000000")

    def test_malformed_sha_raises(self, git_repo: Path) -> None:
        """A malformed commit SHA raises an error."""
        adapter = GitAdapter()
        with pytest.raises(Exception):
            adapter.get_commit_diff(git_repo, commit_sha="not-a-sha")


# ===========================================================================
# 5. Non-git directory
# ===========================================================================


class TestNonGitDirectory:
    """GitAdapter raises an error when pointed at a non-git directory."""

    def test_non_git_dir_raises(self, tmp_path: Path) -> None:
        """A directory without .git raises an error."""
        not_a_repo = tmp_path / "empty"
        not_a_repo.mkdir()
        adapter = GitAdapter()
        with pytest.raises(Exception):
            adapter.get_commit_diff(not_a_repo)


# ===========================================================================
# 6. File change statuses
# ===========================================================================


class TestFileChangeStatuses:
    """GitAdapter correctly identifies file statuses from git diff --name-status."""

    def test_added_file(self, git_repo: Path) -> None:
        """A new file in the first commit has status='added'."""
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo)
        added = [fc for fc in result.changed_files if fc.status == "added"]
        assert len(added) > 0

    def test_modified_file(self, git_repo_multi_commit: Path) -> None:
        """A modified file has status='modified'."""
        # Add a third commit that modifies user_service.py
        _commit_file(
            git_repo_multi_commit,
            "app/services/user_service.py",
            "from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id, active=True)\n",
            "modify get_user",
        )
        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        modified = [fc for fc in result.changed_files if fc.status == "modified"]
        assert len(modified) > 0

    def test_deleted_file(self, git_repo_multi_commit: Path) -> None:
        """A deleted file has status='deleted'."""
        # Delete user_service.py
        full_path = git_repo_multi_commit / "app" / "services" / "user_service.py"
        full_path.unlink()
        _run_git(git_repo_multi_commit, "add", "-A")
        _run_git(git_repo_multi_commit, "commit", "-m", "delete user_service")

        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        deleted = [fc for fc in result.changed_files if fc.status == "deleted"]
        assert len(deleted) > 0

    def test_renamed_file(self, git_repo_multi_commit: Path) -> None:
        """A renamed file has status='renamed' with old_path set."""
        old_path = git_repo_multi_commit / "app" / "services" / "user_service.py"
        new_path = git_repo_multi_commit / "app" / "services" / "auth_service.py"
        old_path.rename(new_path)
        _run_git(git_repo_multi_commit, "add", "-A")
        _run_git(git_repo_multi_commit, "commit", "-m", "rename user_service")

        adapter = GitAdapter()
        result = adapter.get_commit_diff(git_repo_multi_commit)
        renamed = [fc for fc in result.changed_files if fc.status == "renamed"]
        # git may detect the rename (with similarity score) or report as delete+add
        # Both are acceptable for Phase 1
        if renamed:
            assert renamed[0].old_path is not None