"""Extraction logging to JSONL files."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ADRLogEntry:
    timestamp: str
    adr_id: str
    adr_path: str
    model_id: str
    input_text: str
    prompt_instruction: str
    raw_response: dict | None
    constraint_count: int
    parsed_predicate_count: int
    error_count: int
    error_types: list[str]
    extract_error: str | None
    duration_ms: float


def write_log(entry: ADRLogEntry, log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.__dict__, default=str) + "\n")
    except Exception:
        pass