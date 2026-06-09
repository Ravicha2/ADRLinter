"""
ADR constraint extraction via langextract.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import time
from datetime import datetime, timezone

import langextract as lx

from services.models import (
    CommitDiff,
    ConstraintEdge,
    ExtractionError,
    ExtractionResult,
    FileChange,
    PredicateType,
)

# LangExtractConfig

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

# Few-shot examples (must be ExampleData objects, not raw dicts)

PROMPT_DESCRIPTION = (
    "Extract architectural constraints from ADR documents.\n"
    "\n"
    "Predicates:\n"
    "- prohibits_dependency: the subject module must NOT import or call the object module\n"
    "- requires_dependency: the subject module MUST import or call the object module\n"
    "- prohibits_implementation: the subject module must NOT define the logic described by the object\n"
    "- requires_implementation: the subject module MUST define the logic described by the object\n"
    "\n"
    "Scoping:\n"
    "- Use wildcard subjects (e.g., app.services.*) when the ADR constrains an entire namespace\n"
    "- Use specific FQN subjects when the ADR constrains a single module\n"
    "- Never use bare * as a subject\n"
    "- Objects must always be specific FQNs, never wildcards\n"
    "\n"
    "Each constraint has: subject, predicate, object, justification (the natural language reason from the ADR text)."
)

FEW_SHOT_EXAMPLES = [
    lx.data.ExampleData(
        text="Direct MySQL connections are prohibited for services "
             "in the app.services namespace.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.services namespace",
                attributes={
                    "subject": "app.services.*",
                    "predicate": "prohibits_dependency",
                    "object": "mysql.connector",
                    "justification": "Direct MySQL connections are prohibited for services.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="All API endpoints shall implement authentication "
             "through app.auth.middleware.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.auth.middleware",
                attributes={
                    "subject": "app.api.*",
                    "predicate": "requires_implementation",
                    "object": "app.auth.middleware",
                    "justification": "All API endpoints must implement authentication.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="All services in the app.services namespace must "
             "import app.common.logging for structured log output.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.common.logging",
                attributes={
                    "subject": "app.services.*",
                    "predicate": "requires_dependency",
                    "object": "app.common.logging",
                    "justification": "All services must import the structured logging module.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="No module outside app.auth shall implement "
             "authentication logic. Only app.auth.middleware is permitted "
             "to define authentication behavior.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.auth.middleware",
                attributes={
                    "subject": "app.auth.*",
                    "predicate": "prohibits_implementation",
                    "object": "app.auth.middleware",
                    "justification": "Only app.auth.middleware may define authentication behavior.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="We will use Black for code formatting and isort for "
             "import sorting. Line length is set to 88 characters.",
        extractions=[],
    ),
]


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


# ADR file detection
_ADR_ID_RE = re.compile(r"^(ADR-\d+)", re.IGNORECASE)


def is_adr_file(file_change: FileChange, adr_dir: str) -> bool:
    """Check if a FileChange points to an ADR markdown file."""
    prefix = adr_dir if adr_dir.endswith("/") else adr_dir + "/"
    return file_change.path.startswith(prefix) and file_change.path.endswith(".md")


def _parse_adr_id(path: str) -> str:
    """Extract ADR ID from a filename like 'ADR-001-mysql-storage.md'."""
    stem = Path(path).stem
    match = _ADR_ID_RE.match(stem)
    if match:
        return match.group(1).upper()
    return stem


# ADRExtractor

class ADRExtractor:
    def __init__(self, config: LangExtractConfig, log_path: Path | None = None) -> None:
        self.config = config
        self.log_path = log_path

    def extract_constraints(
        self, adr_text: str, adr_id: str, adr_path: str
    ) -> ExtractionResult:
        start = time.perf_counter()
        extract_error: str | None = None
        raw_response: dict | None = None

        try:
            model_config = lx.factory.ModelConfig(
                model_id=self.config.model_id,
                provider=self.config.provider,
                provider_kwargs={
                    "api_key": self.config.api_key,
                    "base_url": self.config.model_url,
                    "temperature": self.config.temperature,
                },
            )
            result = lx.extract(
                text_or_documents=adr_text,
                prompt_description=PROMPT_DESCRIPTION,
                examples=FEW_SHOT_EXAMPLES,
                config=model_config,
                prompt_validation_level=lx.prompt_validation.PromptValidationLevel.OFF,
            )
            if result.extractions is not None:
                raw_response = {
                    "extractions": [
                        {
                            "extraction_text": e.extraction_text,
                            "extraction_class": e.extraction_class,
                            "attributes": e.attributes,
                            "char_interval": (
                                (e.char_interval.start_pos, e.char_interval.end_pos)
                                if e.char_interval else None
                            ),
                        }
                        for e in result.extractions
                    ]
                }
        except Exception as exc:
            extract_error = str(exc)
            return ExtractionResult(
                errors=[ExtractionError(
                    message=extract_error,
                    adr_path=adr_path,
                    error_type="api_failure",
                )]
            )
        finally:
            duration_ms = (time.perf_counter() - start) * 1000

        constraints: list[ConstraintEdge] = []
        errors: list[ExtractionError] = []
        parsed_predicate_count = 0

        for ext in result.extractions or []:
            if ext.char_interval is None:
                errors.append(ExtractionError(
                    message=f"Extraction missing char_interval: {ext.extraction_text}",
                    adr_path=adr_path,
                    error_type="malformed_extraction",
                ))
                continue

            attrs = ext.attributes or {}
            pred_str = attrs.get("predicate", "")
            try:
                predicate = PredicateType(pred_str)
                parsed_predicate_count += 1
            except ValueError:
                errors.append(ExtractionError(
                    message=f"Invalid predicate '{pred_str}' in: {ext.extraction_text}",
                    adr_path=adr_path,
                    error_type="parse_failure",
                ))
                continue

            try:
                edge = ConstraintEdge(
                    subject=attrs.get("subject", ""),
                    predicate=predicate,
                    object=attrs.get("object", ""),
                    justification=attrs.get("justification", ""),
                    char_interval=(ext.char_interval.start_pos, ext.char_interval.end_pos),
                    adr_id=adr_id,
                    adr_path=adr_path,
                )
                constraints.append(edge)
            except ValueError as exc:
                errors.append(ExtractionError(
                    message=str(exc),
                    adr_path=adr_path,
                    error_type="malformed_extraction",
                ))

        if self.log_path is not None:
            entry = ADRLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                adr_id=adr_id,
                adr_path=adr_path,
                model_id=self.config.model_id or "unknown",
                input_text=adr_text,
                prompt_instruction=PROMPT_DESCRIPTION,
                raw_response=raw_response,
                constraint_count=len(constraints),
                parsed_predicate_count=parsed_predicate_count,
                error_count=len(errors),
                error_types=[e.error_type for e in errors],
                extract_error=extract_error,
                duration_ms=duration_ms,
            )
            self._write_log(entry)

        return ExtractionResult(constraints=constraints, errors=errors)

    def _write_log(self, entry: ADRLogEntry) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.__dict__, default=str) + "\n")
        except Exception:
            pass

    def extract_from_file(self, adr_path: Path) -> ExtractionResult:
        try:
            text = adr_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ExtractionResult(
                errors=[ExtractionError(
                    message=f"ADR file not found: {adr_path}",
                    adr_path=str(adr_path),
                    error_type="file_not_found",
                )]
            )
        adr_id = _parse_adr_id(str(adr_path))
        return self.extract_constraints(text, adr_id, str(adr_path))

    def extract_from_directory(self, adr_dir: Path) -> list[ExtractionResult]:
        try:
            adr_files = sorted(adr_dir.glob("ADR-*.md"))
        except OSError as exc:
            return [ExtractionResult(
                errors=[ExtractionError(
                    message=f"Cannot read ADR directory: {exc}",
                    adr_path=str(adr_dir),
                    error_type="directory_unavailable",
                )]
            )]
        if not adr_files:
            return []
        return [self.extract_from_file(f) for f in adr_files]


# Orchestration: commit pipeline and seed build

def extract_changed_adrs(
    diff: CommitDiff, adr_dir: str, config: LangExtractConfig, log_path: Path | None = None
) -> list[ExtractionResult]:
    """Extract constraints from ADR files that changed (incremental pipeline)."""
    extractor = ADRExtractor(config, log_path=log_path)
    results: list[ExtractionResult] = []
    for change in diff.changed_files:
        if is_adr_file(change, adr_dir):
            if change.path in diff.file_contents:
                content = diff.file_contents[change.path].decode("utf-8", errors="replace")
                adr_id = _parse_adr_id(change.path)
                result = extractor.extract_constraints(content, adr_id, change.path)
                results.append(result)
            else:
                results.append(ExtractionResult(
                    errors=[ExtractionError(
                        message=f"ADR content not available for: {change.path}",
                        adr_path=change.path,
                        error_type="content_unavailable",
                    )]
                ))
    return results


def extract_all_adrs(
    repo_path: Path, adr_dir: str, config: LangExtractConfig, log_path: Path | None = None
) -> list[ExtractionResult]:
    """Extract constraints from all ADR files (seed build)."""
    extractor = ADRExtractor(config, log_path=log_path)
    return extractor.extract_from_directory(repo_path / adr_dir)


def write_constraints(results: list[ExtractionResult], output_path: Path) -> None:
    """Write extracted constraints to JSON for the Merge Layer."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    constraints: list[dict] = []
    errors: list[dict] = []
    for result in results:
        for c in result.constraints:
            constraints.append({
                "subject": c.subject,
                "predicate": c.predicate.value,
                "object": c.object,
                "justification": c.justification,
                "char_interval": list(c.char_interval),
                "adr_id": c.adr_id,
                "adr_path": c.adr_path,
            })
        for e in result.errors:
            errors.append({
                "error_type": e.error_type,
                "message": e.message,
                "adr_path": e.adr_path,
            })
    output_path.write_text(json.dumps({"constraints": constraints, "errors": errors}, indent=2), encoding="utf-8")