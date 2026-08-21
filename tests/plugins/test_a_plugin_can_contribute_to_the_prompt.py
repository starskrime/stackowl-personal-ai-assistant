"""The eighth extension point: a plugin may add a part to the system prompt.

BAKIR, 2026-08-21, answering E2: *"everything, including prompt contributors."*

Chosen AGAINST the recommendation, and the trade is recorded rather than buried —
designs/D16.3.md keeps the original argument under a SUPERSEDED banner. The risk is
real: this is third-party code composing the SYSTEM PROMPT, on every turn, uncovered by
consent, riding the frozen prefix for the life of an incarnation. Seven extension points
already exist with ZERO plugins ever installed, so there is no evidence an eighth was
wanted, and it is a one-way door the moment one third party registers.

WHAT MAKES IT WORKABLE IS D16.1'S PATTERN: the point is CAPABILITY-GATED exactly like
`lifecycle_hooks`. A plugin declaring a contributor without `prompt_contributor` in its
manifest fails to load, loudly. Extensible does not have to mean unguarded.

THE PROPERTY THAT PROTECTS EVERY EXISTING DEPLOYMENT is the first test below: with no
plugins installed — which is every deployment today — the composed prompt must be
BYTE-IDENTICAL. A prompt that shifts because a mechanism was added, not because content
changed, invalidates every live session's cached prefix (Law 1) for nothing.

AND ORDER IS BEHAVIOUR. Plugin parts append AFTER the seven built-ins, sorted by name,
so the cached prefix a session already holds keeps its shape and two plugins cannot
race each other into a different prompt on different boots.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.steps.assemble import (
    PROMPT_PART_NAMES,
    compose_prompt_parts,
)
from stackowl.plugins.capabilities import CAPABILITY_FOR_EXTENSION_POINT
from stackowl.plugins.local_loader import _ABC_NAMES


class TestTheEighthPointIsDeclaredEverywhere:
    """The shape that failed once: D08.2 added `MemoryProvider` to two of the three
    tables that must agree, so registration silently hit `continue` and a plugin
    installed successfully while doing nothing."""

    def test_it_is_in_the_abc_table(self) -> None:
        assert "PromptContributor" in _ABC_NAMES

    def test_it_is_gated_by_a_capability(self) -> None:
        assert CAPABILITY_FOR_EXTENSION_POINT.get("PromptContributor") == (
            "prompt_contributor"
        )

    def test_it_has_somewhere_to_register(self) -> None:
        """A declared point with no registry slot is the exact D08.2 defect."""
        from stackowl.plugins.local_loader import LocalPluginLoader

        loader = LocalPluginLoader()
        assert "PromptContributor" in loader._registries  # noqa: SLF001


class TestNoPluginsMeansNoChange:
    def test_the_prompt_is_byte_identical_with_no_contributors(self) -> None:
        """EVERY deployment today. Adding a mechanism must not move one byte of the
        composed prompt, or every live session's cached prefix is invalidated for
        nothing."""
        rendered = {n: f"<{n}>" for n in PROMPT_PART_NAMES}
        with_mechanism, _a, _f = compose_prompt_parts(rendered, extra={})
        without, _a2, _f2 = compose_prompt_parts(rendered)

        assert with_mechanism == without

    def test_the_built_in_names_are_unchanged(self) -> None:
        """The seven keep their identity and their order — plugin parts are additive,
        never interleaved."""
        assert PROMPT_PART_NAMES[:7] == (
            "base", "capabilities", "persona", "owls",
            "skills", "profile", "stable_context",
        )


class TestAPluginPartIsAppendedDeterministically:
    def test_a_contributed_part_lands_after_the_built_ins(self) -> None:
        """Order is the cached prefix. Appending keeps the shape a live session
        already holds; interleaving would move every part after the insertion."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "BASE"
        prompt, _a, _f = compose_prompt_parts(rendered, extra={"zz": "PLUGIN"})

        assert prompt == "BASE\n\nPLUGIN"

    def test_two_plugins_sort_by_name(self) -> None:
        """Two plugins must not race each other into a different prompt on different
        boots — dict order is insertion order, and plugin load order is filesystem
        order, which is not a contract."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "BASE"
        prompt, _a, _f = compose_prompt_parts(
            rendered, extra={"beta": "B", "alpha": "A"},
        )

        assert prompt == "BASE\n\nA\n\nB"

    def test_a_plugin_part_is_audited_and_measured(self) -> None:
        """Invariant I1 extends to contributed parts: what reaches the prompt reaches
        the cache audit and the size log. A plugin part that could move prompt_hash
        without appearing in the audit would make D01.2 unable to name the culprit."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        _p, audit, fields = compose_prompt_parts(rendered, extra={"mine": "X"})

        assert audit["mine"] == "X"
        assert fields["mine_len"] == 1

    def test_a_plugin_cannot_overwrite_a_built_in_part(self) -> None:
        """The trust boundary that matters most. A contributor named `base` must not be
        able to REPLACE the platform's own base prompt — that would let third-party code
        delete the agent's instructions rather than add to them."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "PLATFORM"
        prompt, audit, _f = compose_prompt_parts(
            rendered, extra={"base": "HIJACKED"},
        )

        assert "HIJACKED" not in (prompt or "")
        assert audit["base"] == "PLATFORM"


class TestAnUngatedPluginCannotRegister:
    def test_the_capability_exists_and_is_named(self) -> None:
        """D16.1 made the capability model real — it had been decorative, read by
        nothing. The eighth point must arrive gated, not trusted."""
        from stackowl.plugins import capabilities

        assert capabilities.PROMPT_CONTRIBUTOR == "prompt_contributor"
        assert "prompt_contributor" in capabilities.ALL_CAPABILITIES


class TestAContributorNeverCostsTheTurn:
    """The three guards, each pinned. A contributor is third-party code on the
    cold-build path of a real conversation — it may fail, but never take the turn
    with it."""

    async def _render(self, contributor: object) -> dict[str, str]:
        from stackowl.pipeline.contributors import (
            PromptContext,
            PromptContributorRegistry,
        )

        reg = PromptContributorRegistry()
        reg.register(contributor)  # type: ignore[arg-type]
        return await reg.render_all(
            PromptContext(owl_name="o", channel="cli", session_key="s")
        )

    @pytest.mark.asyncio
    async def test_a_raising_contributor_contributes_nothing(self) -> None:
        class _Boom:
            name = "boom"

            async def render(self, ctx: object) -> str:
                raise RuntimeError("plugin bug")

        assert await self._render(_Boom()) == {}

    @pytest.mark.asyncio
    async def test_a_hanging_contributor_is_abandoned(self, monkeypatch) -> None:
        """Third-party code must never be able to hold a conversation open."""
        from stackowl.pipeline import contributors as mod

        monkeypatch.setattr(mod, "DEFAULT_RENDER_TIMEOUT_SECONDS", 0.01)

        class _Hang:
            name = "hang"

            async def render(self, ctx: object) -> str:
                import asyncio

                await asyncio.sleep(5)
                return "never"

        assert await self._render(_Hang()) == {}

    @pytest.mark.asyncio
    async def test_a_non_string_is_ignored(self) -> None:
        """A contributor returning the wrong type must not reach the join()."""

        class _Wrong:
            name = "wrong"

            async def render(self, ctx: object) -> str:
                return 42  # type: ignore[return-value]

        assert await self._render(_Wrong()) == {}

    @pytest.mark.asyncio
    async def test_a_good_contributor_is_returned(self) -> None:
        class _Good:
            name = "good"

            async def render(self, ctx: object) -> str:
                return "hello"

        assert await self._render(_Good()) == {"good": "hello"}

    def test_an_unnamed_contributor_is_refused(self) -> None:
        """The name is the audit key AND the log field stem. An unnamed part could
        move prompt_hash with nothing able to say which part moved."""
        from stackowl.pipeline.contributors import PromptContributorRegistry

        class _Anon:
            name = ""

            async def render(self, ctx: object) -> str:
                return "x"

        reg = PromptContributorRegistry()
        reg.register(_Anon())  # type: ignore[arg-type]
        assert reg.names() == ()

    def test_the_context_carries_nothing_per_turn(self) -> None:
        """D01.1's lesson, made unavailable rather than discouraged: the prompt is
        frozen per incarnation, so a contributor that could read per-turn state would
        freeze a session-long falsehood."""
        from stackowl.pipeline.contributors import PromptContext

        fields = set(PromptContext.__dataclass_fields__)
        assert fields == {"owl_name", "channel", "session_key", "lean"}
        assert "intent_class" not in fields
