"""``compute_registry_digest`` -- a stable fingerprint of the registered
journal event types (Spec 2.3, gateway/core Hello exchange).
"""

from __future__ import annotations

from stackowl.journal.digest import compute_registry_digest
from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventRegistry, EventTypeSpec


class _ThrowawayAttrs(JournalAttrsBase):
    pass


def _throwaway_spec(type_name: str, schema_version: int = 1) -> EventTypeSpec:
    return EventTypeSpec(
        type=type_name,
        schema_version=schema_version,
        attrs_model=_ThrowawayAttrs,
        emitting_process="test",
        record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT,
        narrate=lambda attrs, target: "throwaway",
    )


class TestTheDigestIsStableAndDeterministic:
    def test_the_digest_is_stable_across_repeat_calls(self) -> None:
        assert compute_registry_digest() == compute_registry_digest()

    def test_the_digest_does_not_depend_on_registration_order(self) -> None:
        """Two registries holding the SAME set, registered in different
        orders, must produce the same digest -- only the declared set (and
        the attention-policy version) should matter, never insertion order."""
        r1 = EventRegistry()
        r1.register(_throwaway_spec("a.one"))
        r1.register(_throwaway_spec("b.two"))

        r2 = EventRegistry()
        r2.register(_throwaway_spec("b.two"))
        r2.register(_throwaway_spec("a.one"))

        def _digest_for(registry: EventRegistry) -> str:
            pairs = sorted(
                (t, registry.get(t).schema_version) for t in registry.all_types()
            )
            import hashlib

            from stackowl.journal.attention import ATTENTION_POLICY_VERSION

            canonical = (
                "|".join(f"{t}:{v}" for t, v in pairs)
                + f"|attention_policy:{ATTENTION_POLICY_VERSION}"
            )
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

        assert _digest_for(r1) == _digest_for(r2)


class TestTheDigestChangesWhenTheRegistrySetChanges:
    def test_registering_a_new_type_changes_the_digest(
        self, monkeypatch,
    ) -> None:
        before = compute_registry_digest()

        from stackowl.journal import registry as registry_module

        fresh = registry_module.EventRegistry()
        # Seed the fresh registry with everything the real one already has,
        # PLUS one new throwaway type -- proves the digest reacts to the SET,
        # without permanently mutating the process-global singleton.
        for type_name in registry_module.get_registry().all_types():
            fresh.register(registry_module.get_registry().get(type_name))
        fresh.register(_throwaway_spec("test.throwaway_for_digest"))

        monkeypatch.setattr(registry_module, "_registry", fresh)

        after = compute_registry_digest()
        assert after != before

    def test_a_changed_schema_version_changes_the_digest(self, monkeypatch) -> None:
        from stackowl.journal import registry as registry_module

        fresh = registry_module.EventRegistry()
        fresh.register(_throwaway_spec("test.versioned", schema_version=1))
        monkeypatch.setattr(registry_module, "_registry", fresh)
        first = compute_registry_digest()

        fresh2 = registry_module.EventRegistry()
        fresh2.register(_throwaway_spec("test.versioned", schema_version=2))
        monkeypatch.setattr(registry_module, "_registry", fresh2)
        second = compute_registry_digest()

        assert first != second
