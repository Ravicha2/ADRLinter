"""Tests for GitAdapter: fetch Diff from a real git repository.

Public interface under test:
    GitAdapter.get_diff(repo_path, to_sha=None, from_sha=None) -> Diff
    GitAdapter.get_pr_diff(repo_path, base_ref, head_ref) -> Diff

These are integration tests that create real git repos via subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.cpt import GitAdapter
from services.models import Diff, FileChange


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

    def test_returns_diff(self, git_repo: Path) -> None:
        """get_diff returns a Diff object."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        assert isinstance(result, Diff)

    def test_to_sha_populated(self, git_repo: Path) -> None:
        """to_sha matches the actual HEAD commit."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        expected_sha = _run_git(git_repo, "rev-parse", "HEAD")
        assert result.to_sha == expected_sha

    def test_from_sha_is_none(self, git_repo: Path) -> None:
        """First commit has from_sha=None."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        assert result.from_sha is None

    def test_first_commit_all_files_added(self, git_repo: Path) -> None:
        """First commit reports all files as 'added'."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        for fc in result.changed_files:
            assert fc.status == "added"

    def test_first_commit_file_contents(self, git_repo: Path) -> None:
        """First commit file_contents contains the committed files."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        assert "app/models/user.py" in result.file_contents
        content = result.file_contents["app/models/user.py"]
        assert b"class User" in content

    def test_first_commit_from_contents_empty(self, git_repo: Path) -> None:
        """First commit has empty from_contents."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
        assert result.from_contents == {}


# ===========================================================================
# 2. Multi-commit repo: latest commit
# ===========================================================================


class TestMultiCommitLatest:
    """GitAdapter on HEAD of a multi-commit repo."""

    def test_changed_files_only_head(self, git_repo_multi_commit: Path) -> None:
        """Only the most recent commit's files appear in changed_files."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit)
        # Last commit added user_service.py, so it should be in changed_files
        paths = {fc.path for fc in result.changed_files}
        assert "app/services/user_service.py" in paths

    def test_from_sha_populated(self, git_repo_multi_commit: Path) -> None:
        """Non-first commit has a from_sha."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit)
        assert result.from_sha is not None

    def test_file_contents_has_new_version(self, git_repo_multi_commit: Path) -> None:
        """file_contents contains the file at the commit SHA."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit)
        for fc in result.changed_files:
            if fc.status != "deleted":
                assert fc.path in result.file_contents

    def test_from_contents_has_old_version(self, git_repo_multi_commit: Path) -> None:
        """from_contents contains the file at the from_sha for modified/deleted files."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit)
        for fc in result.changed_files:
            if fc.status in ("modified", "deleted"):
                assert fc.path in result.from_contents


# ===========================================================================
# 3. Specific commit SHA
# ===========================================================================


class TestSpecificCommit:
    """GitAdapter can target a specific commit by SHA."""

    def test_specific_to_sha(self, git_repo_multi_commit: Path) -> None:
        """Passing to_sha returns the diff for that specific commit."""
        # Get the first commit's SHA
        shas = _run_git(git_repo_multi_commit, "log", "--format=%H", "--reverse").split("\n")
        first_sha = shas[0]

        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit, to_sha=first_sha)
        assert result.to_sha == first_sha
        assert result.from_sha is None  # first commit

    def test_default_is_head(self, git_repo_multi_commit: Path) -> None:
        """Not passing to_sha defaults to HEAD."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo_multi_commit)
        head_sha = _run_git(git_repo_multi_commit, "rev-parse", "HEAD")
        assert result.to_sha == head_sha


# ===========================================================================
# 4. Invalid commit SHA
# ===========================================================================


class TestInvalidCommitSHA:
    """GitAdapter raises an error for invalid commit SHAs."""

    def test_invalid_sha_raises(self, git_repo: Path) -> None:
        """A non-existent commit SHA raises an error."""
        adapter = GitAdapter()
        with pytest.raises(Exception):
            adapter.get_diff(git_repo, to_sha="0000000000000000000000000000000000000000")

    def test_malformed_sha_raises(self, git_repo: Path) -> None:
        """A malformed commit SHA raises an error."""
        adapter = GitAdapter()
        with pytest.raises(Exception):
            adapter.get_diff(git_repo, to_sha="not-a-sha")


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
            adapter.get_diff(not_a_repo)


# ===========================================================================
# 6. File change statuses
# ===========================================================================


class TestFileChangeStatuses:
    """GitAdapter correctly identifies file statuses from git diff --name-status."""

    def test_added_file(self, git_repo: Path) -> None:
        """A new file in the first commit has status='added'."""
        adapter = GitAdapter()
        result = adapter.get_diff(git_repo)
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
        result = adapter.get_diff(git_repo_multi_commit)
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
        result = adapter.get_diff(git_repo_multi_commit)
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
        result = adapter.get_diff(git_repo_multi_commit)
        renamed = [fc for fc in result.changed_files if fc.status == "renamed"]
        # git may detect the rename (with similarity score) or report as delete+add
        # Both are acceptable for Phase 1
        if renamed:
            assert renamed[0].old_path is not None


# ===========================================================================
# 7. get_pr_diff: merge-base (three-dot) diff
# ===========================================================================


@pytest.fixture
def git_repo_branch(tmp_path: Path) -> Path:
    """Create a repo with a main branch and a feature branch that diverged.

    main:     A --- C (modify user.py)
                 \\
    feature:   B (add service.py)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@test.com")
    _run_git(repo, "config", "user.name", "Test")

    # Commit A: initial on default branch, then rename to main
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n",
        "A: initial commit",
    )
    _run_git(repo, "branch", "-m", "main")

    # Create feature branch from A
    _run_git(repo, "checkout", "-b", "feature")
    # Commit B: add service on feature
    _commit_file(
        repo,
        "app/services/user_service.py",
        "from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n",
        "B: add user service",
    )

    # Switch back to main, advance with commit C
    _run_git(repo, "checkout", "main")
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n",
        "C: add User.all method",
    )

    return repo


class TestGetPrDiff:
    """GitAdapter.get_pr_diff uses three-dot merge-base diff."""

    def test_returns_diff(self, git_repo_branch: Path) -> None:
        """get_pr_diff returns a Diff object."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch, base_ref="main", head_ref="feature")
        assert isinstance(result, Diff)

    def test_shas_are_resolved(self, git_repo_branch: Path) -> None:
        """to_sha and from_sha are full commit SHAs, not branch names."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch, base_ref="main", head_ref="feature")
        # SHAs should be 40 hex chars
        assert len(result.to_sha) == 40
        assert len(result.from_sha) == 40

    def test_merge_base_semantics(self, git_repo_branch: Path) -> None:
        """Three-dot diff only shows changes on the feature branch, not main."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch, base_ref="main", head_ref="feature")
        paths = {fc.path for fc in result.changed_files}
        # Feature branch added service.py, so it should appear.
        # Main modified user.py but that should NOT appear in the three-dot diff.
        assert "app/services/user_service.py" in paths
        assert "app/models/user.py" not in paths

    def test_invalid_base_raises(self, git_repo_branch: Path) -> None:
        """An invalid base ref raises ValueError."""
        adapter = GitAdapter()
        with pytest.raises(ValueError, match="Invalid base_ref"):
            adapter.get_pr_diff(git_repo_branch, base_ref="nonexistent", head_ref="feature")

    def test_invalid_head_raises(self, git_repo_branch: Path) -> None:
        """An invalid head ref raises ValueError."""
        adapter = GitAdapter()
        with pytest.raises(ValueError, match="Invalid head_ref"):
            adapter.get_pr_diff(git_repo_branch, base_ref="main", head_ref="nonexistent")


# ===========================================================================
# 8. get_pr_diff: add/modify/delete on feature branch with divergent base
# ===========================================================================


@pytest.fixture
def git_repo_branch_all_changes(tmp_path: Path) -> Path:
    """Create a repo where the feature branch adds, modifies, and deletes files,
    while the base branch also has divergent changes.

    main:     A --- C (modify user.py)
                 \\
    feature:   B (add service.py) --- D (modify user.py, delete helper.py)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@test.com")
    _run_git(repo, "config", "user.name", "Test")

    # Commit A: initial files on default branch
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n",
        "A: initial commit",
    )
    _commit_file(
        repo,
        "app/utils/helper.py",
        "def assist():\n    return True\n",
        "A: add helper",
    )
    _run_git(repo, "branch", "-m", "main")

    # Create feature branch from A
    _run_git(repo, "checkout", "-b", "feature")
    # Commit B: add new file on feature
    _commit_file(
        repo,
        "app/services/user_service.py",
        "from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n",
        "B: add user service",
    )
    # Commit D: modify existing file and delete helper on feature
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n",
        "D: modify user.py on feature",
    )
    # delete helper.py on feature
    (repo / "app" / "utils" / "helper.py").unlink()
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "D: delete helper.py on feature")

    # Switch back to main, advance with commit C (divergent change)
    _run_git(repo, "checkout", "main")
    _commit_file(
        repo,
        "app/models/user.py",
        "class User:\n    def find(self):\n        pass\n\n    def find_by_email(self, email):\n        pass\n",
        "C: modify user.py on main",
    )

    return repo


class TestGetPrDiffAllChanges:
    """get_pr_diff correctly handles add, modify, and delete on the feature branch."""

    def test_added_file_on_feature_branch(self, git_repo_branch_all_changes: Path) -> None:
        """Files added on the feature branch appear in changed_files."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        paths = {fc.path for fc in result.changed_files}
        assert "app/services/user_service.py" in paths

    def test_modified_file_on_feature_branch(self, git_repo_branch_all_changes: Path) -> None:
        """Files modified on the feature branch appear with status='modified'."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        modified = [fc for fc in result.changed_files if fc.path == "app/models/user.py"]
        assert len(modified) == 1
        assert modified[0].status == "modified"

    def test_deleted_file_on_feature_branch(self, git_repo_branch_all_changes: Path) -> None:
        """Files deleted on the feature branch appear with status='deleted'."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        deleted = [fc for fc in result.changed_files if fc.path == "app/utils/helper.py"]
        assert len(deleted) == 1
        assert deleted[0].status == "deleted"

    def test_divergent_base_changes_excluded(self, git_repo_branch_all_changes: Path) -> None:
        """Three-dot diff excludes changes that only exist on the base branch."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        # Main added find_by_email but feature did not, so user.py changes
        # are from the feature branch (its modification), not from main's divergence.
        # The key assertion: every changed file is one the feature branch touched.
        feature_paths = {
            "app/services/user_service.py",  # added on feature
            "app/models/user.py",             # modified on feature
            "app/utils/helper.py",            # deleted on feature
        }
        result_paths = {fc.path for fc in result.changed_files}
        assert result_paths == feature_paths

    def test_file_contents_new_version(self, git_repo_branch_all_changes: Path) -> None:
        """file_contents has the feature-branch version of non-deleted files."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        assert b"def all" in result.file_contents["app/models/user.py"]
        assert b"get_user" in result.file_contents["app/services/user_service.py"]
        # deleted file should not have new content
        assert "app/utils/helper.py" not in result.file_contents

    def test_from_contents_old_version(self, git_repo_branch_all_changes: Path) -> None:
        """from_contents has the merge-base version of modified/deleted files."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        # from_sha is the merge-base, so from_contents reflects the merge-base state
        # user.py at merge-base has only find(), not all() (feature) or find_by_email() (main)
        assert b"def all" not in result.from_contents["app/models/user.py"]
        assert b"find_by_email" not in result.from_contents["app/models/user.py"]
        # helper.py was present at merge-base and deleted on feature
        assert b"def assist" in result.from_contents["app/utils/helper.py"]
        # added file should not have old content
        assert "app/services/user_service.py" not in result.from_contents

    def test_from_sha_is_merge_base(self, git_repo_branch_all_changes: Path) -> None:
        """from_sha is the merge-base SHA, not the base branch tip."""
        adapter = GitAdapter()
        result = adapter.get_pr_diff(git_repo_branch_all_changes, base_ref="main", head_ref="feature")
        merge_base = _run_git(git_repo_branch_all_changes, "merge-base", "main", "feature")
        assert result.from_sha == merge_base