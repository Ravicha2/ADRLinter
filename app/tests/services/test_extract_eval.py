"""Integration tests for langextract ADR constraint extraction with LLM-as-judge.

These tests call real LLM APIs and are marked @pytest.mark.integration.
Run with: pytest -m integration
Skip with: pytest -m "not integration"

Requires environment variable for API key (OPENROUTER_API_KEY by default).
Tests auto-skip if the key is not set.

Judge evaluation:
    1. Extract constraints from ADR text using the configured LLM
    2. Present extracted constraints + original ADR text to a judge LLM
    3. Judge scores each extraction on correctness of subject, predicate, object
    4. Assert overall extraction quality meets a minimum threshold
"""

from __future__ import annotations

import os
import json
from pathlib import Path

import requests
import pytest
from services.adr_extract import ADRExtractor, LangExtractConfig
from services.models import PredicateType


HAS_API_KEY = bool(os.environ.get("OPENROUTER_API_KEY"))
API_KEY = os.getenv("OPENROUTER_API_KEY")

# Module-level cache to avoid redundant LLM API calls across test classes.
# Keyed by adr_id so the same ADR is only extracted once per session.
_extraction_cache: dict[str, object] = {}


def _extract_cached(extractor, adr_text: str, adr_id: str, adr_path: str):
    """Return cached extraction result, calling the API only on first access."""
    if adr_id not in _extraction_cache:
        _extraction_cache[adr_id] = extractor.extract_constraints(
            adr_text=adr_text, adr_id=adr_id, adr_path=adr_path,
        )
    return _extraction_cache[adr_id]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_API_KEY, reason="OPENROUTER_API_KEY is missing from environment")
]

@pytest.fixture
def extractor():
    """Create an ADRExtractor with real LLM backend."""
    if not HAS_API_KEY:
        pytest.skip(f"{API_KEY} not set")
    from services.adr_extract import ADRExtractor, LangExtractConfig

    config = LangExtractConfig()
    return ADRExtractor(config=config, log_path=Path("logs/adr_extract.jsonl"))


# ---------------------------------------------------------------------------
# Sample ADR texts for evaluation
# ---------------------------------------------------------------------------

ADR_FORBIDDEN_DEP = """\
# ADR-001: MySQL Storage Layer

## Status: Accepted

## Decision

The app.database.query module is the only permitted interface for database
access. All services must route queries through this interface. Direct MySQL
connections are prohibited for services in the app.services namespace.
No module outside app.database shall import mysql.connector directly.
"""

ADR_REQUIRED_IMPL = """\
# ADR-003: Authentication Middleware

## Status: Accepted

## Decision

All API endpoints must implement authentication through the app.auth.middleware
component. No other module is permitted to implement authentication logic.
Public endpoints at app.api.public are exempt from authentication requirements
but must not access user data directly.
"""

ADR_NO_CONSTRAINTS = """\
# ADR-006: Code Style Guide

## Status: Accepted

## Decision

We will use Black for code formatting and isort for import sorting.
Line length is set to 88 characters. No architectural constraints apply.
"""

ADR_REQUIRED_DEP = """\
# ADR-007: Centralized Logging

## Status: Accepted

## Decision

All services in the app.services namespace must import app.common.logging
for structured log output. No service shall use print() or the bare logging
module directly.
"""

ADR_PROHIBITED_IMPL = """\
# ADR-008: Authentication Centralization

## Status: Accepted

## Decision

No module outside app.auth shall implement authentication logic.
Only app.auth.middleware is permitted to define authentication behavior.
Other modules must call app.auth.middleware to perform authentication checks.
"""


# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """\
You are evaluating the quality of ADR constraint extractions.

Given an original ADR document and a list of extracted constraints, score each
constraint on three dimensions:

1. **Subject correctness**: Is the subject FQN correct and appropriately scoped?
2. **Predicate correctness**: Is the predicate correct for this constraint?
   - prohibits_dependency: the subject must NOT import or call the object
   - requires_dependency: the subject MUST import or call the object
   - prohibits_implementation: the subject must NOT define the logic described by the object
   - requires_implementation: the subject MUST define the logic described by the object
   - Dependency = subject's imports/calls are constrained
   - Implementation = subject's internal code (what it defines) is constrained
3. **Object correctness**: Is the object FQN correct and appropriately scoped?

For each extracted constraint, you must perform a step-by-step analysis BEFORE scoring.
In your `analysis` field, explicitly justify:
- Why the Subject FQN matches or fails the expected scope.
- Why the Predicate matches or fails the architectural intent.
- Why the Object FQN matches or fails the expected scope.

Respond STRICTLY with JSON format using the exact schema below. Do not include markdown formatting, backticks, or preamble.

{
  "evaluation": [
    {
      "constraint_index": <int>,
      "analysis": "<step-by-step reasoning for the Subject, Predicate, and Object>",
      "subject_correct": <boolean>,
      "predicate_correct": <boolean>,
      "object_correct": <boolean>,
      "overall": "<'correct' | 'partially_correct' | 'incorrect'>"
    }
  ],
  "overall_feedback": "<brief summary of common extraction failures or scoping issues>",
  "total_expected": <int>,
  "total_extracted": <int>,
  "precision": <float>,
  "recall": <float>
}
"""


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestExtractForbiddenDependency:
    """Extract prohibits_dependency constraints from ADR-001."""

    @pytest.fixture
    def result(self, extractor):
        """Extract constraints from ADR-001 (cached across tests)."""
        return _extract_cached(
            extractor, ADR_FORBIDDEN_DEP, "ADR-001",
            "docs/adr/ADR-001-mysql-storage.md",
        )

    def test_extracts_at_least_one_constraint(self, result) -> None:
        """ADR-001 should produce at least one prohibits_dependency constraint."""
        assert len(result.constraints) >= 1

    def test_prohibits_dependency_found(self, result) -> None:
        """At least one constraint should be prohibits_dependency."""
        predicates = {c.predicate.value for c in result.constraints}
        assert "prohibits_dependency" in predicates

    def test_no_api_errors(self, result) -> None:
        """Extraction should not produce API errors."""
        api_errors = [e for e in result.errors if e.error_type == "api_failure"]
        assert len(api_errors) == 0, f"API errors: {[e.message for e in api_errors]}"


class TestExtractRequiredImplementation:
    """Extract requires_implementation constraints from ADR-003."""

    @pytest.fixture
    def result(self, extractor):
        return _extract_cached(
            extractor, ADR_REQUIRED_IMPL, "ADR-003",
            "docs/adr/ADR-003-auth-middleware.md",
        )

    def test_extracts_at_least_one_constraint(self, result) -> None:
        """ADR-003 should produce at least one constraint."""
        assert len(result.constraints) >= 1

    def test_requires_implementation_found(self, result) -> None:
        """At least one constraint should be requires_implementation."""
        predicates = {c.predicate.value for c in result.constraints}
        assert "requires_implementation" in predicates


class TestExtractNoConstraints:
    """An ADR with no enforceable constraints produces empty results."""

    @pytest.fixture
    def result(self, extractor):
        return _extract_cached(
            extractor, ADR_NO_CONSTRAINTS, "ADR-006",
            "docs/adr/ADR-006-code-style.md",
        )

    def test_no_constraints_found(self, result) -> None:
        """ADR-006 has no architectural constraints."""
        assert len(result.constraints) == 0

    def test_no_errors(self, result) -> None:
        """No errors for a valid ADR with no constraints."""
        assert len(result.errors) == 0


class TestExtractRequiredDependency:
    """Extract requires_dependency constraints from ADR-007."""

    @pytest.fixture
    def result(self, extractor):
        return _extract_cached(
            extractor, ADR_REQUIRED_DEP, "ADR-007",
            "docs/adr/ADR-007-centralized-logging.md",
        )

    def test_extracts_at_least_one_constraint(self, result) -> None:
        """ADR-007 should produce at least one constraint."""
        assert len(result.constraints) >= 1

    def test_requires_dependency_found(self, result) -> None:
        """At least one constraint should be requires_dependency."""
        predicates = {c.predicate.value for c in result.constraints}
        assert "requires_dependency" in predicates


class TestExtractProhibitsImplementation:
    """Extract prohibits_implementation constraints from ADR-008."""

    @pytest.fixture
    def result(self, extractor):
        return _extract_cached(
            extractor, ADR_PROHIBITED_IMPL, "ADR-008",
            "docs/adr/ADR-008-auth-centralization.md",
        )

    def test_extracts_at_least_one_constraint(self, result) -> None:
        """ADR-008 should produce at least one constraint."""
        assert len(result.constraints) >= 1

    def test_prohibits_implementation_found(self, result) -> None:
        """At least one constraint should be prohibits_implementation."""
        predicates = {c.predicate.value for c in result.constraints}
        assert "prohibits_implementation" in predicates


class TestJudgePrompt:
    """JUDGE_PROMPT references all four predicates with definitions."""

    def test_judge_prompt_contains_all_predicates(self) -> None:
        assert "prohibits_dependency" in JUDGE_PROMPT
        assert "requires_implementation" in JUDGE_PROMPT
        assert "requires_dependency" in JUDGE_PROMPT
        assert "prohibits_implementation" in JUDGE_PROMPT

    def test_judge_prompt_defines_dependency_boundary(self) -> None:
        assert "import" in JUDGE_PROMPT.lower() or "call" in JUDGE_PROMPT.lower()


class TestJudgeEvaluation:
    MINIMUM_SCORE = 0.7

    @pytest.fixture
    def adr001_extraction(self, extractor):
        """Reuse cached ADR-001 extraction instead of another API call."""
        return _extract_cached(
            extractor, ADR_FORBIDDEN_DEP, "ADR-001",
            "docs/adr/ADR-001-mysql-storage.md",
        )

    def _serialize_constraints(self, constraints: list) -> str:
        return json.dumps(
            [
                {
                    "subject": c.subject,
                    "predicate": c.predicate.value,
                    "object": c.object,
                    "justification": c.justification,
                    "char_interval": list(c.char_interval),
                }
                for c in constraints
            ],
            indent=2,
        )

    def _call_judge(self, adr_text: str, constraints_json: str) -> dict:
        """Ask the LLM to score an extraction. Returns parsed JSON scores."""
        judge_prompt = (
            f"{JUDGE_PROMPT}\n\n"
            f"## Original ADR\n\n{adr_text}\n\n"
            f"## Extracted Constraints\n\n```json\n{constraints_json}\n```\n\n"
            "Respond with JSON only. No preamble, no markdown fences."
        )

        model_name = os.getenv("JUDGE_MODEL")

        if not API_KEY:
            raise ValueError("OPENROUTER_API_KEY is missing from environment.")

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user", 
                    "content": judge_prompt
                }
            ]
        }

        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        response_data = response.json()

        raw = response_data["choices"][0]["message"]["content"]
        clean = raw.strip()
        # Strip markdown code fences if present
        if clean.startswith("```"):
            first_newline = clean.index("\n") if "\n" in clean else len(clean)
            clean = clean[first_newline + 1:]
        if clean.endswith("```"):
            clean = clean[: -3]
        clean = clean.strip()
        return json.loads(clean)

    def test_judge_scores_above_threshold(self, adr001_extraction) -> None:
        if adr001_extraction.errors:
            api_errors = [e for e in adr001_extraction.errors if e.error_type == "api_failure"]
            if api_errors:
                pytest.skip(f"API errors: {[e.message for e in api_errors]}")

        assert len(adr001_extraction.constraints) >= 1

        for c in adr001_extraction.constraints:
            assert c.char_interval[0] >= 0
            assert c.char_interval[1] > c.char_interval[0]
            assert c.justification

        constraints_json = self._serialize_constraints(adr001_extraction.constraints)

        try:
            scores = self._call_judge(ADR_FORBIDDEN_DEP, constraints_json)
        except Exception as e:
            pytest.skip(f"Judge call failed: {e}")

        precision = scores.get("precision", 0.0)
        recall = scores.get("recall", 0.0)

        assert precision >= self.MINIMUM_SCORE, (
            f"Judge precision {precision:.2f} below {self.MINIMUM_SCORE}.\n"
            f"Full scores:\n{json.dumps(scores, indent=2)}"
        )
        assert recall >= self.MINIMUM_SCORE, (
            f"Judge recall {recall:.2f} below {self.MINIMUM_SCORE}.\n"
            f"Full scores:\n{json.dumps(scores, indent=2)}"
        )


class TestDeterminism:
    """Verify that temperature=0.0 produces deterministic extraction."""

    @pytest.fixture
    def first_result(self, extractor):
        return extractor.extract_constraints(
            adr_text=ADR_FORBIDDEN_DEP,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

    @pytest.fixture
    def second_result(self, extractor):
        return extractor.extract_constraints(
            adr_text=ADR_FORBIDDEN_DEP,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

    def test_same_input_same_output(
        self, first_result, second_result
    ) -> None:
        """Running extraction twice on the same ADR should produce the same constraints.

        Note: This test is inherently flaky because LLM outputs can vary
        even at temperature=0.0. It is documented as a known risk (R4 in
        the decision log). If it fails, it indicates non-determinism in
        the LLM provider, not a bug in the extraction module.
        """
        first_subjects = {c.subject for c in first_result.constraints}
        second_subjects = {c.subject for c in second_result.constraints}
        assert first_subjects == second_subjects, (
            f"Non-deterministic extraction: {first_subjects} != {second_subjects}"
        )