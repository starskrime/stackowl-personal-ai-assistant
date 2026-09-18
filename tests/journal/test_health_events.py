"""``health.changed`` is registered correctly (Story 2.6, FR84): AMBIENT (not
a give-up itself -- the ambient/needs-you split belongs to the heal/job give-up
types, not to observing a transition), a bounded ``error_code`` from the
closed :class:`~stackowl.journal.enums.HealthErrorCode`, and it narrates.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.journal import AttentionClass, RecordKind, classify
from stackowl.journal.health_events import HealthChangedAttrs
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry


class TestHealthChangedIsRegistered:
    def test_registered_under_record_kind_health_and_ambient(self) -> None:
        spec = get_registry().get("health.changed")
        assert spec.attrs_model is HealthChangedAttrs
        assert spec.record_kind is RecordKind.HEALTH
        assert spec.emitting_process == "scheduler.handlers.health_sweep"
        assert classify("health.changed") == (AttentionClass.AMBIENT, None)


class TestHealthChangedNarrates:
    def test_names_previous_and_new_status(self) -> None:
        spec = get_registry().get("health.changed")
        attrs = HealthChangedAttrs(
            previous_status="down", new_status="ok", error_code=None,
        )
        text = narrate_full(spec, attrs, "db")
        assert "db" in text
        assert "down" in text
        assert "ok" in text


class TestHealthChangedAttrsNeverCarriesExceptionText:
    def test_error_code_accepts_a_closed_code_not_free_text(self) -> None:
        # A bounded label (<=64 chars) is accepted -- the closed HealthErrorCode
        # values all fit comfortably.
        attrs = HealthChangedAttrs(
            previous_status="ok", new_status="down", error_code="connection_refused",
        )
        assert attrs.error_code == "connection_refused"

    def test_error_code_is_optional(self) -> None:
        attrs = HealthChangedAttrs(previous_status="down", new_status="ok", error_code=None)
        assert attrs.error_code is None

    def test_an_oversized_string_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            HealthChangedAttrs(
                previous_status="ok", new_status="down", error_code="x" * 65,
            )

    def test_an_undeclared_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            HealthChangedAttrs(
                previous_status="ok", new_status="down",
                message="a raw exception string must never fit here",  # type: ignore[call-arg]
            )
