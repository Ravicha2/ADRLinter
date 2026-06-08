"""
ADR constraint extraction via langextract.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

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
    api_key_env: str = "OLLAMA_API_KEY"
    judge_model_id: str | None = None
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.model_id is None:
            self.model_id = os.getenv("LANDEXTRACT_MODEL_ID", "")
        if self.model_url is None:
            self.model_url = os.getenv("LANDEXTRACT_MODEL_URL", "http://localhost:11434")
        if self.judge_model_id is None:
            self.judge_model_id = os.getenv("LANDEXTRACT_JUDGE_MODEL_ID", "")

    @classmethod
    def from_dict(cls, raw: dict) -> LangExtractConfig:
        return cls(
            model_id=raw.get("model_id", os.getenv("LANDEXTRACT_MODEL_ID", "")),
            model_url=raw.get("model_url", os.getenv("LANDEXTRACT_MODEL_URL", "http://localhost:11434")),
            api_key_env=raw.get("api_key_env", "OLLAMA_API_KEY"),
            judge_model_id=raw.get("judge_model_id", os.getenv("LANDEXTRACT_JUDGE_MODEL_ID", "")),
            temperature=raw.get("temperature", 0.0),
        )

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

# Few-shot examples (must be ExampleData objects, not raw dicts)

PROMPT_DESCRIPTION = (
    "Extract architectural constraints from ADR documents. "
    "Each constraint has a subject (FQN or wildcard), a predicate "
    "(prohibits_dependency or requires_implementation), an object (FQN or wildcard), "
    "and a justification (the natural language reason from the ADR text)."
)

FEW_SHOT_EXAMPLES = [
    lx.data.ExampleData(
        text="Direct MySQL connections are prohibited for services "
             "in the app.services namespace.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.services.* prohibits_dependency app.db.mysql",
                attributes={
                    "subject": "app.services.*",
                    "predicate": "prohibits_dependency",
                    "object": "app.db.mysql",
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
                extraction_text="app.api.* requires_implementation app.auth.middleware",
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
        text="The users.views module is forbidden from importing "
             "users.models directly; all access must go through the service layer.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="users.views.* prohibits_dependency users.models.*",
                attributes={
                    "subject": "users.views.*",
                    "predicate": "prohibits_dependency",
                    "object": "users.models.*",
                    "justification": "Views are forbidden from importing models directly.",
                },
            )
        ],
    ),
]


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
    def __init__(self, config: LangExtractConfig) -> None:
        self.config = config

    def extract_constraints(
        self, adr_text: str, adr_id: str, adr_path: str
    ) -> ExtractionResult:
        try:
            result = lx.extract(
                text_or_documents=adr_text,
                prompt_description=PROMPT_DESCRIPTION,
                examples=FEW_SHOT_EXAMPLES,
                model_id=self.config.model_id,
                api_key=self.config.api_key,
                model_url=self.config.model_url,
                temperature=self.config.temperature,
            )
        except Exception as exc:
            return ExtractionResult(
                errors=[ExtractionError(
                    message=str(exc),
                    adr_path=adr_path,
                    error_type="api_failure",
                )]
            )

        constraints: list[ConstraintEdge] = []
        errors: list[ExtractionError] = []

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

        return ExtractionResult(constraints=constraints, errors=errors)

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
        except OSError:
            return []
        if not adr_files:
            return []
        return [self.extract_from_file(f) for f in adr_files]


# Orchestration: commit pipeline and seed build

def extract_changed_adrs(
    diff: CommitDiff, adr_dir: str, config: LangExtractConfig
) -> list[ExtractionResult]:
    """Extract constraints from ADR files that changed (incremental pipeline)."""
    extractor = ADRExtractor(config)
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
    repo_path: Path, adr_dir: str, config: LangExtractConfig
) -> list[ExtractionResult]:
    """Extract constraints from all ADR files (seed build)."""
    extractor = ADRExtractor(config)
    return extractor.extract_from_directory(repo_path / adr_dir)


def write_constraints(results: list[ExtractionResult], output_path: Path) -> None:
    """Write extracted constraints to JSON for the Merge Layer."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for result in results:
        for c in result.constraints:
            entries.append({
                "subject": c.subject,
                "predicate": c.predicate.value,
                "object": c.object,
                "justification": c.justification,
                "char_interval": list(c.char_interval),
                "adr_id": c.adr_id,
                "adr_path": c.adr_path,
            })
        for e in result.errors:
            entries.append({
                "error_type": e.error_type,
                "message": e.message,
                "adr_path": e.adr_path,
            })
    output_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")