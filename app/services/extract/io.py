"""ADR file detection and path utilities."""
from __future__ import annotations

import re
from pathlib import Path

from services.models import FileChange

_ADR_ID_RE = re.compile(r"^(\d+)", re.IGNORECASE)


def is_adr_file(file_change: FileChange, adr_dir: str) -> bool:
    """Check if a FileChange points to an ADR markdown file."""
    prefix = adr_dir if adr_dir.endswith("/") else adr_dir + "/"
    return file_change.path.startswith(prefix) and file_change.path.endswith(".md")


def parse_adr_id(path: str) -> str:
    """Extract ADR ID from a filename like '001-mysql-storage.md'."""
    stem = Path(path).stem
    match = _ADR_ID_RE.match(stem)
    if match:
        return match.group(1)
    return stem