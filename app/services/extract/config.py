"""Extraction configuration. No external LLM library imports."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LangExtractConfig:
    model_id: str | None = None
    model_url: str | None = None
    api_key_env: str = "OPENROUTER_API_KEY"
    provider: str = "openai"
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.model_id is None:
            self.model_id = os.getenv("LANGEXTRACT_MODEL_ID", "google/gemini-3.1-flash-lite")
        if self.model_url is None:
            self.model_url = os.getenv("LANGEXTRACT_MODEL_URL", "https://openrouter.ai/api/v1")

    @classmethod
    def from_dict(cls, raw: dict) -> LangExtractConfig:
        return cls(
            model_id=raw.get("model_id"),
            model_url=raw.get("model_url"),
            api_key_env=raw.get("api_key_env", "OPENROUTER_API_KEY"),
            provider=raw.get("provider", "openai"),
            temperature=raw.get("temperature", 0.0),
        )

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)