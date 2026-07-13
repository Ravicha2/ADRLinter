"""ADR file detection, ID parsing, and status extraction utilities."""
from __future__ import annotations

import re
from pathlib import Path

from services.models import ADRStatus, FileChange

_ADR_ID_RE = re.compile(r"^(\d+)", re.IGNORECASE)
_ADR_STATUS_RE = re.compile(r"^##\s+Status\s*$", re.MULTILINE)


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


def parse_adr_status(adr_text: str) -> ADRStatus:
    """Parse the ## Status field from ADR markdown.

    Returns ACCEPTED for missing, empty, or unrecognized status values.
    Handles common variations: "Accepted", "Superceded by ADR 010",
    "Rejected", case-insensitive.
    """
    match = _ADR_STATUS_RE.search(adr_text)
    if not match:
        return ADRStatus.ACCEPTED

    after = adr_text[match.end():]
    lines = after.split("\n")
    status_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            status_line = stripped.lower()
            break

    if not status_line:
        return ADRStatus.ACCEPTED
    if status_line.startswith("rejected"):
        return ADRStatus.REJECTED
    # ponytail: "superceded" is a common misspelling of "superseded"
    if status_line.startswith("superseded") or status_line.startswith("superceded"):
        return ADRStatus.SUPERSEDED
    if status_line.startswith("accepted"):
        return ADRStatus.ACCEPTED
    return ADRStatus.ACCEPTED