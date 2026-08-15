"""D08.2 slice C — MemoryProvider as a plugin kind.

The item is titled "Memory providers as plugins" and this is that. Slices A and B
cleared the ground (the MemoryBridge split, then the orchestrator removal of the
dead fact half); this is the surface they were clearing it for.

WHAT THE DESIGN SETTLED, and what each test here pins:

  I1  provider-contributed tool schemas never exceed the ceiling (default 6);
      the built-in curated memory is EXEMPT and is not counted
  I2  the active set is resolved ONCE per session and does not change for the
      life of that incarnation (Law 1 — nothing swaps the toolset mid-turn)
  I3  a provider that would breach the ceiling is REFUSED at activation, logged
      at INFO, never silently dropped and never PARTIALLY loaded
  I4  a provider that raises never costs the user a reply
  I6  the built-in curated memory cannot be deactivated by any configuration

I5 (consent/clarify unchanged across slice B) was the guard on the removal and is
covered by the consent smoke suite. I7 (every staged_facts row bounded) shipped
with migration 0114.

WHY THE BUILT-IN IS EXPRESSED THROUGH THE INTERFACE rather than special-cased
around it: an interface with no implementation is a guess. Registering curated
memory as a provider means the contract is exercised by something real on every
boot — but it is exempt from the cap and cannot be removed, because D08.1 shipped
those two files as a guarantee, not an option.
"""

from __future__ import annotations

import pytest

from stackowl.memory.providers import (
    MemoryProviderRegistry,
    ProviderRefused,
)

pytestmark = pytest.mark.asyncio


class _StubProvider:
    """A third-party-shaped provider. Contributes `n` tool schemas."""

    def __init__(self, name: str, n_schemas: int = 1, *, available: bool = True) -> None:
        self._name = name
        self._schemas = [{"name": f"{name}_tool_{i}"} for i in range(n_schemas)]
        self._available = available

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def tool_schemas(self) -> list[dict[str, object]]:
        return list(self._schemas)


class _ExplodingProvider(_StubProvider):
    def is_available(self) -> bool:
        raise RuntimeError("provider blew up during availability probe")


class TestTheCeiling:
    async def test_under_the_ceiling_every_provider_activates(self) -> None:
        reg = MemoryProviderRegistry(ceiling=6)

        active = reg.resolve([_StubProvider("a", 2), _StubProvider("b", 3)])

        assert [p.name for p in active if p.name != "builtin"] == ["a", "b"]

    async def test_the_builtin_does_not_count_against_the_ceiling(self) -> None:
        """I1. D08.1's curated memory is a guarantee, not a budget line — if it
        counted, installing providers could squeeze out the platform's own
        memory."""
        reg = MemoryProviderRegistry(ceiling=1)

        active = reg.resolve([_StubProvider("a", 1)])

        names = [p.name for p in active]
        assert "builtin" in names and "a" in names, names

    async def test_a_provider_over_the_ceiling_is_REFUSED(self) -> None:
        """I3. Refused, not truncated — a partially loaded provider is worse than
        an absent one, because half its tools work."""
        reg = MemoryProviderRegistry(ceiling=3)

        active = reg.resolve([_StubProvider("fits", 3), _StubProvider("surplus", 1)])

        assert [p.name for p in active if p.name != "builtin"] == ["fits"]
        assert reg.refused == [("surplus", 1, 3)], reg.refused

    async def test_a_refusal_is_announced_not_silent(self) -> None:
        """I3. A silently dropped provider is indistinguishable from one that was
        never installed — the operator would debug the wrong thing.

        The handler is attached to `stackowl.memory` DIRECTLY rather than using
        caplog: setup_logging() turns propagation off, so a root-level capture
        would pass or fail depending on whether another test had configured
        logging first. This asserts against the logger the code actually writes to.
        """
        import logging

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("stackowl.memory")
        handler = _Capture(level=logging.INFO)
        previous = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            MemoryProviderRegistry(ceiling=1).resolve(
                [_StubProvider("fits", 1), _StubProvider("surplus", 1)]
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)

        refusals = [r for r in records if "REFUSED" in r.getMessage()]
        assert refusals, f"the refusal was not logged: {[r.getMessage() for r in records]}"
        assert refusals[0].levelno == logging.INFO, (
            "a refusal must be INFO — production runs at INFO, and a DEBUG line "
            "cannot answer 'why is my provider missing?'"
        )
        assert getattr(refusals[0], "_fields", {}).get("provider") == "surplus"

    async def test_a_single_provider_larger_than_the_whole_ceiling_is_refused(
        self,
    ) -> None:
        """The edge the naive running-total misses: one provider that alone
        exceeds the budget must be refused, not admitted because nothing came
        before it."""
        reg = MemoryProviderRegistry(ceiling=2)

        active = reg.resolve([_StubProvider("huge", 5)])

        assert [p.name for p in active if p.name != "builtin"] == []


class TestLaw1:
    async def test_the_active_set_is_frozen_after_resolution(self) -> None:
        """I2. Law 1 — the toolset a session started with is the toolset it keeps.
        Re-resolving mid-incarnation would change the tool schemas under a live
        prompt cache, voiding every turn already in the window."""
        reg = MemoryProviderRegistry(ceiling=6)
        first = reg.resolve([_StubProvider("a", 1)])

        second = reg.resolve([_StubProvider("a", 1), _StubProvider("b", 1)])

        assert second == first, "a second resolve changed the active set"
        assert [p.name for p in second if p.name != "builtin"] == ["a"]

    async def test_the_returned_set_cannot_be_mutated_by_a_caller(self) -> None:
        """Freezing that a caller can append to is not frozen."""
        reg = MemoryProviderRegistry(ceiling=6)
        active = reg.resolve([_StubProvider("a", 1)])

        with pytest.raises((AttributeError, TypeError)):
            active.append(_StubProvider("sneaky", 1))  # type: ignore[attr-defined]


class TestDegradesWithoutCostingAReply:
    async def test_an_unavailable_provider_is_skipped(self) -> None:
        reg = MemoryProviderRegistry(ceiling=6)

        active = reg.resolve([_StubProvider("down", 1, available=False), _StubProvider("up", 1)])

        assert [p.name for p in active if p.name != "builtin"] == ["up"]

    async def test_a_provider_that_RAISES_does_not_break_resolution(self) -> None:
        """I4. A third-party provider is untrusted code running at boot. If it
        can take the platform down by raising, every install is a risk."""
        reg = MemoryProviderRegistry(ceiling=6)

        active = reg.resolve([_ExplodingProvider("bad", 1), _StubProvider("good", 1)])

        assert [p.name for p in active if p.name != "builtin"] == ["good"]

    async def test_the_builtin_survives_every_provider_failing(self) -> None:
        reg = MemoryProviderRegistry(ceiling=6)

        active = reg.resolve([_ExplodingProvider("bad", 1)])

        assert [p.name for p in active] == ["builtin"]


class TestTheBuiltinIsNonRemovable:
    async def test_it_is_present_with_no_providers_at_all(self) -> None:
        assert [p.name for p in MemoryProviderRegistry(ceiling=6).resolve([])] == ["builtin"]

    async def test_a_ceiling_of_zero_still_keeps_it(self) -> None:
        """I6. No configuration deactivates curated memory — a ceiling of 0 means
        'no THIRD-PARTY providers', never 'no memory'."""
        active = MemoryProviderRegistry(ceiling=0).resolve([_StubProvider("a", 1)])

        assert [p.name for p in active] == ["builtin"]

    async def test_a_provider_may_not_impersonate_the_builtin(self) -> None:
        """The name is the exemption. If a plugin could claim it, it would inherit
        both the cap exemption and the non-removability."""
        reg = MemoryProviderRegistry(ceiling=6)

        with pytest.raises(ProviderRefused):
            reg.resolve([_StubProvider("builtin", 99)])


class TestTheManifestKind:
    def test_memory_provider_is_an_accepted_plugin_type(self) -> None:
        from stackowl.plugins.manifest import PluginManifest

        m = PluginManifest(
            name="acme-memory",
            version="1.0.0",
            type="memory_provider",
            entry_point="acme.memory:Provider",
            description="a third-party memory provider",
        )

        assert m.type == "memory_provider"

    def test_an_unknown_kind_is_still_rejected(self) -> None:
        """Adding a value to the Literal must not loosen it into a free string."""
        from pydantic import ValidationError

        from stackowl.plugins.manifest import PluginManifest

        with pytest.raises(ValidationError):
            PluginManifest(
                name="acme",
                version="1.0.0",
                type="not_a_real_kind",  # type: ignore[arg-type]
                entry_point="x:Y",
                description="d",
            )


class TestTheLoaderDrivenPath:
    """`register()` then `resolve()` with no arguments — how the plugin loader
    actually drives this, and the path every other test skipped by passing an
    explicit list. The first version of resolve() raised TypeError here."""

    async def test_resolve_with_no_arguments_uses_registered_candidates(self) -> None:
        reg = MemoryProviderRegistry(ceiling=6)
        reg.register(_StubProvider("from_plugin", 1), source_name="acme-memory")

        active = reg.resolve()

        assert [p.name for p in active] == ["builtin", "from_plugin"]

    async def test_resolve_with_no_candidates_still_yields_the_builtin(self) -> None:
        assert [p.name for p in MemoryProviderRegistry(ceiling=6).resolve()] == ["builtin"]

    async def test_registration_is_not_activation(self) -> None:
        """A candidate that breaches the ceiling is still REFUSED at resolve time.
        If register() activated, the cap could only ever be first-come-first-served.
        """
        reg = MemoryProviderRegistry(ceiling=1)
        reg.register(_StubProvider("fits", 1))
        reg.register(_StubProvider("surplus", 1))

        assert reg.active == (), "registration must not activate anything"
        active = reg.resolve()
        assert [p.name for p in active if p.name != "builtin"] == ["fits"]
        assert [r[0] for r in reg.refused] == ["surplus"]
