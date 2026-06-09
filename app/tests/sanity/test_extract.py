"""Sanity check: verify langextract can connect to the LLM and produce sound output.

Run with: uv run python tests/sanity/test_langextract.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from services.extract import ADRExtractor
from services.extract import LangExtractConfig
from services.models import PredicateType

# Load .env from project root so LANDEXTRACT_MODEL_ID etc. are available
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

ADR_TEXT = """\
# ADR-001: MySQL Storage Layer

## Status: Accepted

## Decision

The app.database.query module is the only permitted interface for database
access. All services must route queries through this interface. Direct MySQL
connections are prohibited for services in the app.services namespace.
No module outside app.database shall import mysql.connector directly.
"""

# Use env vars but fall back to localhost for local runs
# (Docker uses host.docker.internal, which doesn't resolve outside containers)
model_url = os.getenv("LANGEXTRACT_MODEL_URL", "https://openrouter.ai/api/v1")

config = LangExtractConfig(model_url=model_url)
extractor = ADRExtractor(config=config)

# 1. LLM connectivity
result = extractor.extract_constraints(
    adr_text=ADR_TEXT,
    adr_id="ADR-001",
    adr_path="docs/adr/ADR-001-mysql-storage.md",
)

print("=== LangExtract smoke test ===")
print(f"  model_id: {config.model_id}")
print(f"  model_url: {config.model_url}")
print(f"  constraints: {len(result.constraints)}")
print(f"  errors: {len(result.errors)}")
for err in result.errors:
    print(f"    [{err.error_type}] {err.message}")

# 2. No API errors
api_errors = [e for e in result.errors if e.error_type == "api_failure"]
if api_errors:
    print(f"\n  FAIL: API errors: {[e.message for e in api_errors]}")
else:
    print("  OK: no API errors")

# 3. Output structure is sound
if result.constraints:
    c = result.constraints[0]
    print(f"\n  First constraint:")
    print(f"    subject: {c.subject}")
    print(f"    predicate: {c.predicate.value}")
    print(f"    object: {c.object}")
    print(f"    justification: {c.justification}")
    print(f"    char_interval: {c.char_interval}")

    assert c.subject, "subject must be non-empty"
    assert c.object, "object must be non-empty"
    assert c.justification, "justification must be non-empty"
    assert isinstance(c.predicate, PredicateType), f"predicate must be PredicateType, got {type(c.predicate)}"
    assert c.char_interval[0] >= 0, f"char_interval start must be >= 0, got {c.char_interval[0]}"
    assert c.char_interval[1] > c.char_interval[0], f"char_interval end must be > start"
    print("\n  OK: output structure is sound")
else:
    print("\n  WARN: no constraints extracted (LLM may have returned empty)")