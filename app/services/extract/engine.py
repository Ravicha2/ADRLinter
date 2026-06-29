"""Core extraction engine. The ONLY module that imports langextract.

ADRExtractor calls the LLM, parses responses, validates constraints,
and logs results.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import langextract as lx

log = logging.getLogger(__name__)

from services.extract.config import LangExtractConfig
from services.extract.io import parse_adr_id
from services.extract.logging import ADRLogEntry, write_log
from services.extract.prompts import FEW_SHOT_EXAMPLES, PROMPT_DESCRIPTION
from services.models import (
    ExtractionError,
    ExtractionResult,
    PredicateType,
    SymbolicConstraint,
)


def derive_package_context(adg: "ADG") -> list[str]:  # noqa: F821
    """Return all top-level module packages from the ADG, excluding 'tests'.

    The LLM uses this list to pick role_general values instead of inventing
    FQN strings. Every top-level module is included so both subject and object
    general roles have a bounded vocabulary to draw from.
    """
    if not adg.nodes:
        return []
    top_modules: set[str] = set()
    for n in adg.nodes:
        parts = n.fqn.parts
        if not parts or len(parts) <= 1:
            continue
        top = parts[0]
        if top == "tests":
            continue
        top_modules.add(top)
    return sorted(top_modules)


class ADRExtractor:
    def __init__(
        self,
        config: LangExtractConfig,
        log_path: Path | None = None,
        package_context: list[str] | None = None,
    ) -> None:
        self.config = config
        self.log_path = log_path
        self.package_context = package_context

    def _build_prompt(self) -> str:
        if not self.package_context:
            return PROMPT_DESCRIPTION
        packages = ", ".join(self.package_context)
        return (
            PROMPT_DESCRIPTION
            + "\nCodebase packages (use these as role_general values):\n"
            + packages
            + "\n"
        )

    def extract_constraints(
        self, adr_text: str, adr_id: str, adr_path: str
    ) -> ExtractionResult:
        start = time.perf_counter()
        log.info("extract_constraints: starting extraction for %s (%s)", adr_id, adr_path)
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
                prompt_description=self._build_prompt(),
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
                        }
                        for e in result.extractions
                    ]
                }
        except Exception as exc:
            extract_error = str(exc)
            log.error("extract_constraints: LLM call failed for %s: %s", adr_id, extract_error)
            return ExtractionResult(
                errors=[ExtractionError(
                    message=extract_error,
                    adr_path=adr_path,
                    error_type="api_failure",
                )]
            )
        finally:
            duration_ms = (time.perf_counter() - start) * 1000

        constraints: list[SymbolicConstraint] = []
        errors: list[ExtractionError] = []
        parsed_predicate_count = 0

        extraction_count = len(result.extractions) if result.extractions else 0
        log.info("extract_constraints: LLM returned %d raw extractions for %s", extraction_count, adr_id)

        for ext in result.extractions or []:
            attrs = ext.attributes or {}
            pred_str = attrs.get("predicate", "")
            try:
                predicate = PredicateType(pred_str)
                parsed_predicate_count += 1
            except ValueError:
                log.warning("extract_constraints: invalid predicate '%s' in %s", pred_str, adr_id)
                errors.append(ExtractionError(
                    message=f"Invalid predicate '{pred_str}' in: {ext.extraction_text}",
                    adr_path=adr_path,
                    error_type="parse_failure",
                ))
                continue

            try:
                sc = SymbolicConstraint(
                    subject_role_general=attrs.get("subject_role_general", ""),
                    subject_role_specific=attrs.get("subject_role_specific", ""),
                    predicate=predicate,
                    object_role_general=attrs.get("object_role_general", ""),
                    object_role_specific=attrs.get("object_role_specific", ""),
                    justification=attrs.get("justification", ""),
                    extraction_text=ext.extraction_text or "",
                    adr_id=adr_id,
                    adr_path=adr_path,
                )
                constraints.append(sc)
                log.info(
                    "extract_constraints: parsed constraint [%s] '%s' -[%s]-> '%s'",
                    adr_id, sc.subject_role_general, sc.predicate.value, sc.object_role_general,
                )
            except ValueError as exc:
                log.error("extract_constraints: malformed extraction for %s: %s", adr_id, exc)
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

        log.info(
            "extract_constraints: %s done in %.0fms: %d constraints, %d errors",
            adr_id, duration_ms, len(constraints), len(errors),
        )
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
            adr_files = sorted(adr_dir.glob("*.md"))
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