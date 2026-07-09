"""Tests for dismissal model, identity key, and filter logic."""

from services.cpt.dismissal import (
    Dismissal,
    compute_identity_hash,
    compute_identity_key,
    compute_short_id,
    filter_dismissed,
    violation_identity,
    violation_short_id,
)
from services.cpt.resolution import Violation
from services.fqn import FQN
from services.models import ConstraintEdge, PredicateType
from services.resolver import MatchStatus


def _make_violation(
    subject: str = "app.service.*",
    predicate: PredicateType = PredicateType.PROHIBITS_DEPENDENCY,
    object: str = "app.repo.*",
    matched_fqn: str = "app.service.UserService",
    adr_id: str = "ADR-001",
) -> Violation:
    return Violation(
        constraint=ConstraintEdge(
            subject=subject,
            predicate=predicate,
            object=object,
            justification="test",
            adr_id=adr_id,
            adr_path="docs/adr/001.md",
        ),
        changed_fqn=FQN.from_dotted_safe("app.service.UserService"),
        matched_fqn=FQN.from_dotted_safe(matched_fqn),
        match_status=MatchStatus.EXACT,
        evidence="test evidence",
        change_type="structural",
    )


class TestComputeIdentityKey:
    def test_deterministic(self):
        key1 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        key2 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        assert key1 == key2

    def test_different_predicate(self):
        key1 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        key2 = compute_identity_key("app.auth", "requires_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        assert key1 != key2

    def test_different_adr_id(self):
        key1 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        key2 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-004")
        assert key1 != key2

    def test_pipe_delimited(self):
        key = compute_identity_key("a", "b", "c", "d", "e")
        assert key == "a|b|c|d|e"


class TestComputeIdentityHash:
    def test_sha256_hex_length(self):
        key = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        h = compute_identity_hash(key)
        assert len(h) == 64  # SHA-256 hex digest

    def test_deterministic(self):
        key = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        assert compute_identity_hash(key) == compute_identity_hash(key)

    def test_different_keys_different_hashes(self):
        key1 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        key2 = compute_identity_key("app.auth", "requires_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        assert compute_identity_hash(key1) != compute_identity_hash(key2)


class TestComputeShortId:
    def test_five_hex_chars(self):
        sid = compute_short_id("abcdef1234567890" * 4)
        assert len(sid) == 5

    def test_different_hashes_different_short_ids(self):
        key1 = compute_identity_key("app.auth", "prohibits_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        key2 = compute_identity_key("app.auth", "requires_dependency", "app.external.*", "app.external.stripe", "ADR-003")
        assert compute_short_id(compute_identity_hash(key1)) != compute_short_id(compute_identity_hash(key2))


class TestViolationIdentity:
    def test_roundtrip(self):
        v = _make_violation()
        id_key = violation_identity(v)
        expected = compute_identity_key("app.service.*", "prohibits_dependency", "app.repo.*", "app.service.UserService", "ADR-001")
        assert id_key == expected

    def test_predicate_stored_as_value(self):
        v = _make_violation(predicate=PredicateType.REQUIRES_IMPLEMENTATION)
        id_key = violation_identity(v)
        assert "requires_implementation" in id_key


class TestViolationShortId:
    def test_deterministic(self):
        v = _make_violation()
        assert violation_short_id(v) == violation_short_id(v)

    def test_different_violations_different_ids(self):
        v1 = _make_violation(adr_id="ADR-001")
        v2 = _make_violation(adr_id="ADR-002")
        assert violation_short_id(v1) != violation_short_id(v2)


class TestDismissalFromViolation:
    def test_populates_all_fields(self):
        v = _make_violation()
        d = Dismissal.from_violation(v)
        assert d.subject == v.constraint.subject
        assert d.predicate == v.constraint.predicate.value
        assert d.object == v.constraint.object
        assert d.matched_fqn == str(v.matched_fqn)
        assert d.adr_id == v.constraint.adr_id
        assert d.identity_hash == compute_identity_hash(violation_identity(v))
        assert d.short_id == compute_short_id(d.identity_hash)

    def test_dismissed_at_is_iso(self):
        v = _make_violation()
        d = Dismissal.from_violation(v)
        # Should be parseable as ISO format
        from datetime import datetime
        datetime.fromisoformat(d.dismissed_at)


class TestFilterDismissed:
    def test_removes_matching(self):
        v = _make_violation()
        dismissals = [Dismissal.from_violation(v)]
        result = filter_dismissed([v], dismissals)
        assert result == []

    def test_keeps_non_matching(self):
        v1 = _make_violation(adr_id="ADR-001")
        v2 = _make_violation(adr_id="ADR-002")
        dismissals = [Dismissal.from_violation(v1)]
        result = filter_dismissed([v1, v2], dismissals)
        assert len(result) == 1
        assert result[0].constraint.adr_id == "ADR-002"

    def test_empty_dismissals_returns_all(self):
        v = _make_violation()
        result = filter_dismissed([v], [])
        assert len(result) == 1

    def test_empty_violations_returns_empty(self):
        result = filter_dismissed([], [Dismissal.from_violation(_make_violation())])
        assert result == []

    def test_partial_dismissal(self):
        v1 = _make_violation(adr_id="ADR-001")
        v2 = _make_violation(adr_id="ADR-002")
        v3 = _make_violation(adr_id="ADR-003")
        dismissals = [Dismissal.from_violation(v2)]
        result = filter_dismissed([v1, v2, v3], dismissals)
        assert len(result) == 2
        assert all(v.constraint.adr_id != "ADR-002" for v in result)