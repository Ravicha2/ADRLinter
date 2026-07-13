"""Dismissal model and identity key logic for violation persistence.

ADR 012: Dismissals-Only Persistence Model.
Identity key: (subject, predicate, object, matched_fqn, adr_id).
Short ID: first 5 hex chars of SHA-256(identity_key).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.cpt.resolution import Violation


def compute_identity_key(subject: str, predicate: str, object: str, matched_fqn: str, adr_id: str) -> str:
    """Canonical 5-tuple string for a violation. Pipe-delimited."""
    return f"{subject}|{predicate}|{object}|{matched_fqn}|{adr_id}"


def compute_identity_hash(identity_key: str) -> str:
    """SHA-256 hex digest of the identity key."""
    return hashlib.sha256(identity_key.encode("utf-8")).hexdigest()


def compute_short_id(identity_hash: str) -> str:
    """First 5 hex chars of the identity hash. ponytail: 5-char hex, upgrade if collisions observed."""
    return identity_hash[:5]


def violation_identity(violation: Violation) -> str:
    """Identity key string from a Violation object."""
    return compute_identity_key(
        subject=violation.constraint.subject,
        predicate=violation.constraint.predicate.value,
        object=violation.constraint.object,
        matched_fqn=str(violation.matched_fqn),
        adr_id=violation.constraint.adr_id,
    )


def violation_short_id(violation: Violation) -> str:
    """Short ID (5 hex chars) for a Violation."""
    return compute_short_id(compute_identity_hash(violation_identity(violation)))


@dataclass
class Dismissal:
    """A persisted dismissal of a CPT violation.

    Flat node in Neo4j, decoupled from FQNNode/ConstraintEdge
    to survive ADG updates.
    """

    short_id: str
    identity_hash: str
    subject: str
    predicate: str
    object: str
    matched_fqn: str
    adr_id: str
    dismissed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_violation(cls, violation: Violation) -> Dismissal:
        id_key = violation_identity(violation)
        id_hash = compute_identity_hash(id_key)
        return cls(
            short_id=compute_short_id(id_hash),
            identity_hash=id_hash,
            subject=violation.constraint.subject,
            predicate=violation.constraint.predicate.value,
            object=violation.constraint.object,
            matched_fqn=str(violation.matched_fqn),
            adr_id=violation.constraint.adr_id,
        )


def filter_dismissed(violations: list[Violation], dismissals: list[Dismissal]) -> list[Violation]:
    """Remove violations whose identity matches any dismissal."""
    dismissed_hashes = {d.identity_hash for d in dismissals}
    return [
        v for v in violations
        if compute_identity_hash(violation_identity(v)) not in dismissed_hashes
    ]