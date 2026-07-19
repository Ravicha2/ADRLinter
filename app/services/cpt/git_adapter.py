"""GitAdapter: fetch Diff from a git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.models import Diff, FileChange

class GitAdapter:
    """Extract structured commit diff data from a local git repo"""

    def get_diff(
        self, repo_path: Path, to_sha: str | None = None, from_sha: str | None = None
    ) -> Diff:
        """Return a Diff between from_sha and to_sha.

        If to_sha is None, uses HEAD. If from_sha is None, uses to_sha's parent.
        """
        self._verify_repo(repo_path)

        if to_sha is None:
            to_sha = self._git(repo_path, "rev-parse", "HEAD")
        else:
            try:
                to_sha = self._git(repo_path, "rev-parse", to_sha)
            except subprocess.CalledProcessError as e:
                raise ValueError(f"Invalid to_sha: {to_sha}") from e

        if from_sha is None:
            try:
                from_sha = self._git(repo_path, "rev-parse", f"{to_sha}^")
            except subprocess.CalledProcessError:
                from_sha = None
        else:
            try:
                from_sha = self._git(repo_path, "rev-parse", from_sha)
            except subprocess.CalledProcessError as e:
                raise ValueError(f"Invalid from_sha: {from_sha}") from e

        # get changed files with status
        changed_files = self._get_changed_files(repo_path, to_sha, from_sha)

        # read file content at both SHAs
        file_contents = self._read_contents(repo_path, to_sha, changed_files, "new")
        from_contents: dict[str, bytes] = {}

        if from_sha is not None:
            from_contents = self._read_contents(repo_path, from_sha, changed_files, "old")

        return Diff(
            to_sha=to_sha,
            from_sha=from_sha,
            changed_files=changed_files,
            file_contents=file_contents,
            from_contents=from_contents,
        )

    def get_pr_diff(self, repo_path: Path, base_ref: str, head_ref: str) -> Diff:
        """Return a Diff using three-dot merge-base semantics (base...head).

        Resolves both refs to SHAs, then diffs from merge-base to head.
        """
        self._verify_repo(repo_path)

        try:
            from_sha = self._git(repo_path, "rev-parse", base_ref)
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Invalid base_ref: {base_ref}") from e

        try:
            to_sha = self._git(repo_path, "rev-parse", head_ref)
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Invalid head_ref: {head_ref}") from e

        # merge-base SHA for three-dot diff
        merge_base = self._git(repo_path, "merge-base", base_ref, head_ref)

        changed_files = self._get_changed_files(repo_path, to_sha, merge_base)
        file_contents = self._read_contents(repo_path, to_sha, changed_files, "new")
        from_contents = self._read_contents(repo_path, merge_base, changed_files, "old")

        return Diff(
            to_sha=to_sha,
            from_sha=merge_base,
            changed_files=changed_files,
            file_contents=file_contents,
            from_contents=from_contents,
        )

    @staticmethod
    def _git(repo_path: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()

    @staticmethod
    def _verify_repo(repo_path: Path) -> None:
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            raise ValueError(f"Not a git repository: {repo_path}")

    def _get_changed_files(
            self, repo_path: Path, to_sha: str, from_sha: str | None
    ) -> list[FileChange]:
        if from_sha is None:
            # First commit: all file status = added
            paths = self._git(repo_path, "ls-tree", "-r", "--name-only", to_sha)
            return [
                FileChange(path=p, status="added")
                for p in paths.splitlines()
                if p
            ]

        STATUS_MAP = {
            "A": "added",
            "M": "modified",
            "D": "deleted",
            "R": "renamed"
        }

        raw = self._git(
            repo_path, "diff", "--name-status", "-M", f"{from_sha}..{to_sha}"
        )

        changes: list[FileChange] = []
        for line in raw.splitlines():
            if not line:
                continue
            parts = line.split("\t")
            status = STATUS_MAP.get(parts[0][0]) # map first status letter to status name
            if status == "renamed":
                old_path = parts[1]
                new_path = parts[2]
                changes.append(
                    FileChange(path=new_path, status="renamed", old_path=old_path)
                )
            elif status == "added":
                changes.append(FileChange(path=parts[1], status="added"))
            elif status == "modified":
                changes.append(FileChange(path=parts[1], status="modified"))
            elif status == "deleted":
                changes.append(FileChange(path=parts[1], status="deleted"))
        return changes

    def _read_contents(
            self,
            repo_path: Path,
            sha: str,
            files_changed: list[FileChange],
            side: str, # "new" for commit, "old" for parent
    ) -> dict[str, bytes]:
        contents: dict[str, bytes] = {}
        for file_changed in files_changed:
            if side == "new" and file_changed.status == "deleted": continue
            if side == "old" and file_changed.status == "added": continue

            if side == "old" and file_changed.old_path:
                path = file_changed.old_path
            else:
                path = file_changed.path

            try:
                result = subprocess.run(
                    ["git", "show", f"{sha}:{path}"],
                    cwd=repo_path,
                    capture_output=True,
                    check=True
                )
                contents[path] = result.stdout
            except subprocess.CalledProcessError:
                pass
        return contents