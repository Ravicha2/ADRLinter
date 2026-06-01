"""GitAdapter: fetch CommitDiff from a git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.models import CommitDiff, FileChange

class GitAdapter:
    """Extract structured commit diff data from a local git repo"""

    def get_commit_diff(
        self, repo_path: Path, commit_sha: str | None = None
    ) -> CommitDiff:
        """Return a CommitDiff for the given commit (HEAD if None)."""
        self.verify_repo(repo_path)

        if commit_sha is None:
            commit_sha = self._git(repo_path, "rev-parse", "HEAD")
        else:
            try:
                commit_sha = self._git(repo_path, "rev-parse", "commit_sha")
            except subprocess.CalledProcessError as e:
                raise ValueError(f"Invalid commit SHA: {commit_sha}") from e
        
        # 2. Find parent SHA (None for initial commit)
        parent_sha: str | None
        try: 
            parent_sha = self._git(repo_path, "rev-parse", f"{commit_sha}^")
        except subprocess.CalledProcessError:
            parent_sha = None
        
        # 3. get changed files with status
        changed_files = self.get_changed_files(repo_path, commit_sha, parent_sha)

        # 4. read file content at both SHAs
        file_contents = self.read_contents(repo_path, commit_sha, changed_files, "new")
        if parent_sha:
            parent_contents = self.read_contents(repo_path, parent_sha, changed_files, "old")

        return CommitDiff(
            commit_sha=commit_sha,
            parent_sha=parent_sha,
            changed_files=changed_files,
            file_contents=file_contents,
            parent_contents=parent_contents,
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
    def verify_repo(repo_path: Path) -> None:
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            raise ValueError(f"Not a git repository: {repo_path}")
    
    def get_changed_files(
            self, repo_path: Path, commit_sha: str, parent_sha: str | None
    ) -> list[FileChange]:
        if parent_sha is None:
            # First commit all file status = added
            paths = self._git(repo_path, "ls-tree", "-r", "--name-only", commit_sha)
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
            repo_path, "diff", "--name-status", "-M", f"{parent_sha}..{commit_sha}"
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
    
    def read_contents(
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
            except subprocess.CalledProcessError:
                pass
        return contents
