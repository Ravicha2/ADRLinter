"""Core extraction engine. The ONLY module that imports langextract.

ADRExtractor calls the LLM, parses responses, validates constraints,
and logs results.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import langextract as lx

from services.extract.config import LangExtractConfig
from services.extract.io import parse_adr_id
from services.extract.logging import ADRLogEntry, write_log
from services.extract.prompts import FEW_SHOT_EXAMPLES, PROMPT_DESCRIPTION
from services.models import (
    ConstraintEdge,
    ExtractionError,
    ExtractionResult,
    PredicateType,
)


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
            write_log(entry, self.log_path)

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
        adr_id = parse_adr_id(str(adr_path))
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