"""The last step: a memory plugin's tools actually reach the model.

MEASURED before building (2026-08-30): the provider seam was complete right up to
presentation. MemoryProvider is discovered by LocalPluginLoader, registered,
activated, ceiling-checked and frozen — and then `active_schemas()` had no caller,
`.memory_providers` had ZERO readers outside its own tests, and `resolve()`'s
return value was discarded at assembly.py:300. An Obsidian or mem0 plugin would
have installed successfully and contributed nothing, silently.

WHY THE SCHEMAS ARE APPENDED AFTER the budgeted selection rather than competing
inside it: D08.2 settled that provider schemas are capped SEPARATELY at 6 and that
"the cap counts what plugins ADD rather than the total". Putting them through
`tool_count_cap` would let a busy turn silently drop a provider the operator
installed — which is the refuse-don't-truncate rule inverted.
"""

from __future__ import annotations

from stackowl.memory.providers import MemoryProvider, MemoryProviderRegistry


class _Vault(MemoryProvider):
    @property
    def name(self) -> str:
        return "vault"

    def is_available(self) -> bool:
        return True

    def tool_schemas(self) -> list[dict[str, object]]:
        return [{"name": "vault_search", "description": "search the vault"}]

    def handle_tool_call(self, tool_name: str, args: dict[str, object]) -> str:
        return f"vault ran {tool_name} with {sorted(args)}"


def test_an_active_providers_schema_is_offered() -> None:
    from stackowl.memory.provider_surface import provider_schemas

    reg = MemoryProviderRegistry()
    reg.resolve([_Vault()])
    assert [s["name"] for s in provider_schemas(reg)] == ["vault_search"]


def test_NO_registry_means_no_schemas_and_no_crash() -> None:
    """The default state today: no registry wired, no providers installed. The
    surface must be a no-op rather than a source of AttributeErrors on a hot path."""
    from stackowl.memory.provider_surface import provider_schemas

    assert provider_schemas(None) == []


def test_an_UNRESOLVED_registry_offers_nothing() -> None:
    """`active` is empty before resolve(), and reading it must NOT resolve lazily —
    an accidental first call from a request path would freeze the active set at
    whatever happened to be loaded then (Law 1)."""
    from stackowl.memory.provider_surface import provider_schemas

    assert provider_schemas(MemoryProviderRegistry()) == []


async def test_a_provider_tool_call_is_ROUTED_and_run() -> None:
    from stackowl.memory.provider_surface import dispatch_provider_tool

    reg = MemoryProviderRegistry()
    reg.resolve([_Vault()])

    out = await dispatch_provider_tool(reg, "vault_search", {"q": "x"})
    assert out is not None
    assert "vault ran vault_search" in out


async def test_a_tool_NOBODY_owns_returns_None_so_the_caller_can_fall_through() -> None:
    """None, not an error string. The dispatcher must be able to tell "no provider
    owns this" from "a provider ran it and said this", or an ordinary unknown-tool
    error would be replaced by a memory-flavoured one."""
    from stackowl.memory.provider_surface import dispatch_provider_tool

    reg = MemoryProviderRegistry()
    reg.resolve([_Vault()])
    assert await dispatch_provider_tool(reg, "not_ours", {}) is None


async def test_a_RAISING_provider_does_not_break_the_turn() -> None:
    """A provider is untrusted code. It may fail its own call; it may not take the
    turn with it."""
    from stackowl.memory.provider_surface import dispatch_provider_tool

    class _Boom(_Vault):
        def handle_tool_call(self, tool_name: str, args: dict[str, object]) -> str:
            raise RuntimeError("provider exploded")

    reg = MemoryProviderRegistry()
    reg.resolve([_Boom()])

    out = await dispatch_provider_tool(reg, "vault_search", {})
    assert out is not None and "failed" in out.lower()


# --------------------------------------------------------------------------- #
# WIRING. Written after M14 — unwiring the dispatch route from execute() — left
# every test above GREEN. The surface being correct says nothing about execute()
# reaching it, and that gap is this programme's most repeated defect.
#
# execute()'s `_run_with_tools` is far too heavy to drive here, so this asserts
# the two call sites exist and read the registry through the accessor that is
# actually in scope there. A structural check, and labelled as one: it is weaker
# than a behavioural test and it is what stands between a working plugin and a
# silent no-op.
# --------------------------------------------------------------------------- #


def test_execute_PRESENTS_and_DISPATCHES_provider_tools() -> None:
    import inspect

    from stackowl.pipeline.steps import execute as ex

    src = inspect.getsource(ex)
    assert src.count("provider_schemas(get_services().memory_providers)") == 2, (
        "both presentation branches must append provider schemas — the enveloped "
        "path and the budgeted path"
    )
    assert "dispatch_provider_tool(" in src, "no provider dispatch in execute()"


def test_the_registry_reaches_the_pipeline_at_all() -> None:
    """StepServices must carry it, or presentation reads None forever and the
    whole seam is a no-op that looks wired."""
    from stackowl.pipeline.services import StepServices

    assert "memory_providers" in StepServices.__dataclass_fields__
    assert StepServices().memory_providers is None, "the default must be the safe one"
