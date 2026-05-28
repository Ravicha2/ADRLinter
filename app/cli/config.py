from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    id: str
    url: str
    size: str = "small"
    ports: dict[str, int] = field(default_factory=dict)
    neo4j_memory: str = "1g"
    adr_dir: str = "docs/adr"


@dataclass
class GlobalConfig:
    concurrency: dict[str, int] = field(default_factory=dict)
    repos: list[RepoConfig] = field(default_factory=list)

    def get_repo(self, repo_id: str) -> RepoConfig:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        raise ValueError(f"Unknown repo: {repo_id!r}. Available: {[r.id for r in self.repos]}")


def load_config(path: Path | None = None) -> GlobalConfig:
    if path is None:
        path = Path(__file__).resolve().parents[2] / "repos" / "repos.yaml"

    with open(path) as f:
        data = yaml.safe_load(f)

    repos = [
        RepoConfig(
            id=r["id"],
            url=r.get("url", f"./{r['id']}"),
            size=r.get("size", "small"),
            ports=r.get("ports", {}),
            neo4j_memory=r.get("neo4j_memory", "1g"),
            adr_dir=r.get("adr_dir", "docs/adr"),
        )
        for r in data.get("repos", [])
    ]

    return GlobalConfig(
        concurrency=data.get("concurrency", {}),
        repos=repos,
    )