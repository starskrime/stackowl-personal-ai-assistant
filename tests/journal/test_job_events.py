"""The four scheduler job-lifecycle event types are registered correctly
(Story 2.6): the right attrs model, the right attention classification --
``job.parked`` is AD-5's named needs_you/high give-up, the other three are
AMBIENT -- and a narration for each.
"""

from __future__ import annotations

from stackowl.journal import AttentionClass, Intensity, RecordKind, classify
from stackowl.journal.enums import NeedsYouKind
from stackowl.journal.job_events import (
    JobFailedAttrs,
    JobFinishedAttrs,
    JobParkedAttrs,
    JobStartedAttrs,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry


class TestJobEventsAreRegistered:
    def test_all_four_types_are_registered_under_record_kind_job(self) -> None:
        registry = get_registry()
        for type_name, model in (
            ("job.started", JobStartedAttrs),
            ("job.finished", JobFinishedAttrs),
            ("job.failed", JobFailedAttrs),
            ("job.parked", JobParkedAttrs),
        ):
            spec = registry.get(type_name)
            assert spec.attrs_model is model
            assert spec.record_kind is RecordKind.JOB
            assert spec.emitting_process == "scheduler"


class TestJobParkedIsTheNamedGiveUpExample:
    def test_job_parked_is_needs_you_high(self) -> None:
        assert classify("job.parked") == (AttentionClass.NEEDS_YOU, Intensity.HIGH)

    def test_the_other_three_are_ambient(self) -> None:
        for type_name in ("job.started", "job.finished", "job.failed"):
            assert classify(type_name) == (AttentionClass.AMBIENT, None)


class TestJobParkedOpensAnIncidentItem:
    """Story 3.1 (AD-28): ``job.parked`` is wired with
    ``needs_you_kind=INCIDENT``; the other three never declare one."""

    def test_job_parked_declares_needs_you_kind_incident(self) -> None:
        spec = get_registry().get("job.parked")
        assert spec.needs_you_kind is NeedsYouKind.INCIDENT

    def test_the_other_three_declare_no_needs_you_kind(self) -> None:
        for type_name in ("job.started", "job.finished", "job.failed"):
            spec = get_registry().get(type_name)
            assert spec.needs_you_kind is None


class TestJobEventsNarrate:
    def test_started_narrates(self) -> None:
        spec = get_registry().get("job.started")
        text = narrate_full(spec, JobStartedAttrs(handler_name="morning_brief"), "morning_brief")
        assert "morning_brief" in text
        assert "started" in text

    def test_finished_narrates(self) -> None:
        spec = get_registry().get("job.finished")
        text = narrate_full(spec, JobFinishedAttrs(handler_name="check_in"), "check_in")
        assert "check_in" in text
        assert "finished" in text

    def test_failed_narrates(self) -> None:
        spec = get_registry().get("job.failed")
        attrs = JobFailedAttrs(handler_name="goal_execution", failure_count=2)
        text = narrate_full(spec, attrs, "goal_execution")
        assert "goal_execution" in text

    def test_parked_names_the_attempt_count(self) -> None:
        spec = get_registry().get("job.parked")
        attrs = JobParkedAttrs(handler_name="rollover_summary", attempt_count=3)
        text = narrate_full(spec, attrs, "rollover_summary")
        assert "rollover_summary" in text
        assert "3" in text


class TestJobAttrsAreBoundedAndClosed:
    def test_handler_name_beyond_the_bound_is_refused(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JobStartedAttrs(handler_name="x" * 65)

    def test_an_undeclared_field_is_refused(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JobStartedAttrs(handler_name="ok", extra_field="nope")  # type: ignore[call-arg]
