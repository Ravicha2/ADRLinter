from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from services._extract_config import LangExtractConfig


@dataclass
class Neo4jMemoryConfig:
    heap_initial: str = os.getenv("NEO4J_HEAP_INITIAL", "256m")
    heap_max: str = os.getenv("NEO4J_HEAP_MAX", "2048m")
    pagecache: str = os.getenv("NEO4J_PAGECACHE", "512m")


@dataclass
class RepoConfig:
    id: str
    url: str
    size: str = "small"
    ports: dict[str, int] = field(default_factory=dict)
    neo4j_memory: Neo4jMemoryConfig = field(default_factory=Neo4jMemoryConfig)
    adr_dir: str = "docs/adr"


@dataclass
class GlobalConfig:
    concurrency: dict[str, int] = field(default_factory=dict)
    repos: list[RepoConfig] = field(default_factory=list)
    langextract: LangExtractConfig = field(default_factory=LangExtractConfig)

    def get_repo(self, repo_id: str) -> RepoConfig:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        raise ValueError(f"Unknown repo: {repo_id!r}. Available: {[r.id for r in self.repos]}")


def _parse_neo4j_memory(raw: dict | str | None) -> Neo4jMemoryConfig:
    if raw is None:
        return Neo4jMemoryConfig()
    if isinstance(raw, str):
        return Neo4jMemoryConfig(heap_initial="256m", heap_max=raw, pagecache="512m")
    return Neo4jMemoryConfig(
        heap_initial=raw.get("heap_initial", os.getenv("NEO4J_HEAP_INITIAL", "256m")),
        heap_max=raw.get("heap_max", os.getenv("NEO4J_HEAP_MAX", "2048m")),
        pagecache=raw.get("pagecache", os.getenv("NEO4J_PAGECACHE", "512m")),
    )


def load_config(path: Path | None = None) -> GlobalConfig:
    if path is None:
        path = Path(__file__).resolve().parents[2] / "repos" / "repos.yaml"

    with open(path) as f:
        data = yaml.safe_load(f)

    repos = [
        RepoConfig(
            id=repo["id"],
            url=repo.get("url", f"./{repo['id']}"),
            size=repo.get("size", "small"),
            ports=repo.get("ports", {}),
            neo4j_memory=_parse_neo4j_memory(repo.get("neo4j_memory")),
            adr_dir=repo.get("adr_dir", "docs/adr"),
        )
        for repo in data.get("repos", [])
    ]

    langextract_data = data.get("langextract", {})
    langextract_config = LangExtractConfig.from_dict(langextract_data or {})

    return GlobalConfig(
        concurrency=data.get("concurrency", {}),
        repos=repos,
        langextract=langextract_config,
    )