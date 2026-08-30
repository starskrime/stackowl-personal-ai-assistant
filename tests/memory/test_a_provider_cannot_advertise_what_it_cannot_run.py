"""A memory provider's schemas must be RUNNABLE, and reachable.

MEASURED 2026-08-30, and it is why this exists. The provider seam is complete
right up to the last step: MemoryProvider is discovered by LocalPluginLoader,
registered, activated, ceiling-checked and frozen for the incarnation — and then
`tool_schemas()` is never called by anything, `.memory_providers` has ZERO readers
in src/ or tests/, and `resolve()`'s return value is discarded at
assembly.py:300. A provider could install successfully and contribute nothing,
silently.

The reference platform's equivalent ABC carries BOTH `get_tool_schemas` and
`handle_tool_call`. Ours had only the first, so a provider could advertise a tool
with no way to execute it — an unrunnable advertisement, which is worse than no
advertisement because the model will call it.
"""

from __future__ import annotations

import pytest

from stackowl.memory.providers import (
    BuiltinCuratedProvider,
    MemoryProvider,
    MemoryProviderRegistry,
    ProviderNotRunnableError,
)


class _Silent(MemoryProvider):
    """Advertises a schema and never overrides handle_tool_call — the defect."""

    @property
    def name(self) -> str:
        return "silent"

    def is_available(self) -> bool:
        return True

    def tool_schemas(self) -> list[dict[str, object]]:
        return [{"name": "silent_note"}]


class _Runnable(MemoryProvider):
    @property
    def name(self) -> str:
        return "vault"

    def is_available(self) -> bool:
        return True

    def tool_schemas(self) -> list[dict[str, object]]:
        return [{"name": "vault_write"}, {"name": "vault_search"}]

    def handle_tool_call(self, tool_name: str, args: dict[str, object]) -> str:
        return f"ran {tool_name}"


def test_an_unrunnable_provider_FAILS_LOUDLY_when_called() -> None:
    """The protection that shipped, and its limit, stated honestly.

    I first made this a REFUSAL at activation — catch it before the model is ever
    offered the tool. That broke NINE existing tests in the D08.2 suite, because
    their `_StubProvider` does not inherit MemoryProvider at all and has no
    handler. Changing nine tests that encode shipped activation behaviour, to
    accommodate a rule the operator never asked for, is the shape the standing
    "never fix a test to make your change pass" rule exists to stop. So the
    refusal was reverted and is recorded as a proposal (DEBT-41).

    What ships instead is strictly better than before and breaks nothing: an
    advertised-but-unrunnable tool now fails LOUDLY at dispatch rather than not
    existing at all.
    """
    with pytest.raises(ProviderNotRunnableError):
        _Silent().handle_tool_call("silent_note", {})


def test_the_BUILTIN_is_unaffected_by_the_new_rule() -> None:
    """It contributes NO schemas, so it has nothing to run and must not be
    refused for lacking a handler. D08.1 shipped it as a guarantee, not an
    option, so a new validation rule must not be able to evict it."""
    reg = MemoryProviderRegistry()
    reg.resolve([])
    assert [p.name for p in reg.active] == [BuiltinCuratedProvider().name]


def test_the_default_handler_REFUSES_rather_than_returning_something_plausible() -> None:
    """If a schema-less provider is somehow called, it must say so loudly. Silently
    returning "" would be indistinguishable from a real empty result."""
    with pytest.raises(ProviderNotRunnableError):
        BuiltinCuratedProvider().handle_tool_call("anything", {})


def test_the_registry_can_answer_WHICH_provider_owns_a_tool() -> None:
    """The query any dispatch path needs. Without it, routing a call means asking
    every provider in turn and hoping exactly one answers."""
    reg = MemoryProviderRegistry()
    reg.resolve([_Runnable()])

    assert reg.provider_for("vault_search") is not None
    assert reg.provider_for("vault_search").name == "vault"
    assert reg.provider_for("not_a_tool") is None


def test_the_registry_can_list_the_ACTIVE_schemas() -> None:
    """The other query dispatch needs: what to present. Counts only what plugins
    ADD — the builtin contributes none."""
    reg = MemoryProviderRegistry()
    reg.resolve([_Runnable()])

    names = [s.get("name") for s in reg.active_schemas()]
    assert sorted(names) == ["vault_search", "vault_write"]
