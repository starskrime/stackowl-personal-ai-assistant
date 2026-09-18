"""The three heal-attempt/verify event types are registered correctly
(Story 2.6): ``heal.exhausted`` is AD-5's named needs_you/high give-up,
``heal.attempted``/``heal.healed`` are AMBIENT, and each narrates.
"""

from __future__ import annotations

from stackowl.journal import AttentionClass, Intensity, RecordKind, classify
from stackowl.journal.heal_events import (
    HealAttemptedAttrs,
    HealExhaustedAttrs,
    HealHealedAttrs,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry


class TestHealEventsAreRegistered:
    def test_all_three_types_are_registered_under_record_kind_heal(self) -> None:
        registry = get_registry()
        for type_name, model in (
            ("heal.attempted", HealAttemptedAttrs),
            ("heal.healed", HealHealedAttrs),
            ("heal.exhausted", HealExhaustedAttrs),
        ):
            spec = registry.get(type_name)
            assert spec.attrs_model is model
            assert spec.record_kind is RecordKind.HEAL
            assert spec.emitting_process == "scheduler.handlers.health_sweep"


class TestHealExhaustedIsTheNamedGiveUpExample:
    def test_heal_exhausted_is_needs_you_high(self) -> None:
        assert classify("heal.exhausted") == (AttentionClass.NEEDS_YOU, Intensity.HIGH)

    def test_attempted_and_healed_are_ambient(self) -> None:
        for type_name in ("heal.attempted", "heal.healed"):
            assert classify(type_name) == (AttentionClass.AMBIENT, None)


class TestHealEventsNarrate:
    def test_attempted_narrates(self) -> None:
        spec = get_registry().get("heal.attempted")
        text = narrate_full(spec, HealAttemptedAttrs(attempt_count=1), "db")
        assert "db" in text

    def test_healed_narrates(self) -> None:
        spec = get_registry().get("heal.healed")
        text = narrate_full(spec, HealHealedAttrs(attempt_count=1), "provider:openai")
        assert "provider:openai" in text

    def test_exhausted_names_the_attempt_count(self) -> None:
        spec = get_registry().get("heal.exhausted")
        text = narrate_full(spec, HealExhaustedAttrs(attempt_count=2), "browser")
        assert "browser" in text
        assert "2" in text
