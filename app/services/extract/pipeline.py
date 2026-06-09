"""ADR constraint extraction pipeline. Orchestrates extraction across ADR files."""
from __future__ import annotations

import json
from pathlib import Path

from services.extract.config import LangExtractConfig
from services.extract.engine import ADRExtractor
from services.extract.io import is_adr_file, parse_adr_id
from services.extract.logging import ADRLogEntry
from services.extract.prompts import FEW_SHOT_EXAMPLES, PROMPT_DESCRIPTION
from services.models import CommitDiff, ExtractionError, ExtractionResult


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
                adr_id = parse_adr_id(change.path)
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