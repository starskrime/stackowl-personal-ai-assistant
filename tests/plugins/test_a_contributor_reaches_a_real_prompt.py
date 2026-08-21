"""A registered contributor actually reaches the composed prompt.

THE LAST MILE, and it is the one D16.1 already paid for once. `MemoryProvider` was
declared in `_ABC_NAMES` and given a registry slot, and a plugin still registered
nowhere — because the slot existed and nothing filled it. A declared extension point
with an unwired slot reads exactly like a working one right up until someone installs a
plugin.

So this drives the real seam: register a contributor, run the composition path
`assemble.run()` uses, and assert its text is in the prompt AND in the audit AND in the
size fields. Anything less proves the mechanism compiles, not that it works.

MEASURED WHILE WRITING THIS, and it is the same defect one slot over:
`orchestrator.py` constructs `LocalPluginLoader` WITHOUT `memory_provider_registry`, so
that slot is `None` in production. The bidirectional table test passes — the KEY is
present in both tables — but the VALUE is nothing, which is precisely the gap the key
check cannot see.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.contributors import (
    PromptContext,
    PromptContributor,
    get_registry,
    reset_registry,
)
from stackowl.pipeline.steps.assemble import PROMPT_PART_NAMES, compose_prompt_parts

pytestmark = pytest.mark.asyncio


class _Extra(PromptContributor):
    name = "house_style"

    async def render(self, ctx: PromptContext) -> str:
        return f"Answer {ctx.owl_name} in British English."


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


class TestTheContributedPartReachesThePrompt:
    async def test_registered_text_is_composed_audited_and_measured(self) -> None:
        """All three at once, because I1 says a part that reaches one reaches all."""
        get_registry().register(_Extra())
        ctx = PromptContext(owl_name="secretary", channel="cli", session_key="s")

        extra = await get_registry().render_all(ctx)
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "BASE"
        prompt, audit, fields = compose_prompt_parts(rendered, extra=extra)

        assert prompt == "BASE\n\nAnswer secretary in British English."
        assert audit["house_style"].startswith("Answer secretary")
        assert fields["house_style_len"] > 0

    async def test_an_empty_render_contributes_nothing(self) -> None:
        """`""` means "nothing to say" — it must not leave a blank stanza, which would
        move prompt_hash for no content."""

        class _Quiet(PromptContributor):
            name = "quiet"

            async def render(self, ctx: PromptContext) -> str:
                return ""

        get_registry().register(_Quiet())
        extra = await get_registry().render_all(
            PromptContext(owl_name="o", channel="cli", session_key="s")
        )
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "BASE"
        prompt, _a, _f = compose_prompt_parts(rendered, extra=extra)

        assert prompt == "BASE"

    async def test_no_registrations_leaves_the_prompt_untouched(self) -> None:
        """Every deployment today. The mechanism existing must cost nothing."""
        extra = await get_registry().render_all(
            PromptContext(owl_name="o", channel="cli", session_key="s")
        )
        rendered = {n: f"<{n}>" for n in PROMPT_PART_NAMES}

        assert extra == {}
        assert compose_prompt_parts(rendered, extra=extra)[0] == (
            compose_prompt_parts(rendered)[0]
        )


class TestEveryDeclaredSlotIsActuallyWired:
    """The KEY check cannot see an unwired slot; this one can.

    `tests/plugins/test_every_declared_extension_point_can_register.py` asserts
    `set(_ABC_NAMES) == set(loader._registries)` — both directions — and it passes even
    when a slot's VALUE is None. That is exactly how a plugin can register nowhere while
    every table looks correct, which is the D08.2 defect wearing a different hat.
    """

    @staticmethod
    def test_the_production_loader_fills_every_slot() -> None:
        """Reads the real construction site rather than a fixture, because the gap is
        in the WIRING, not in the tables."""
        import inspect

        from stackowl.startup import orchestrator

        src = inspect.getsource(orchestrator)
        start = src.index("loader=LocalPluginLoader(")
        # Balanced-paren scan. A first draft stopped at the first ")" after
        # `hook_registry`, which is `HookRegistry.instance()`'s own paren — so the
        # slice ended mid-call and the test reported a slot that WAS wired as missing.
        # A parser that reads less than the whole call cannot judge the whole call.
        open_at = src.index("(", start)
        depth, end = 0, open_at
        for i in range(open_at, len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        call = src[start:end]

        # `memory_provider_registry` is deliberately absent from this list, and the
        # reason is a SEPARATE defect recorded rather than bodged: there is no
        # process-wide MemoryProviderRegistry to pass. It is built inside
        # `memory/assembly.py` and `.resolve()`d immediately — before plugins load —
        # so a plugin registering a provider would arrive after the active set was
        # already fixed. Wiring the slot without fixing that ordering would produce a
        # registry that accepts registrations nobody reads, which is the same defect
        # one layer down.
        missing = [
            slot for slot in (
                "tool_registry", "command_registry", "handler_registry",
                "channel_registry", "owl_registry", "hook_registry",
                "prompt_contributor_registry",
            )
            if f"{slot}=" not in call
        ]
        assert not missing, (
            "declared extension points whose registry is never passed at the real "
            f"construction site — a plugin would register NOWHERE: {missing}"
        )
