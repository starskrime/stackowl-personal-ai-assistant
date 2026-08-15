"""D01.1 slice 4b — the skills block becomes a stable catalogue.

Bakir's Q9: "Names + descriptions ALWAYS loaded; full body fetched on demand via
tool call." The word that decides the shape is *always*.

WHAT WAS THERE. assemble scored owned skills against state.query_embedding
(score_owned_skills -> assign_tiers, deciding which got FULL bodies — both removed
in ESC-10 once assemble stopped scoring), then filled
a global catalogue via hybrid_recall(query_text, query_vec) or
semantic_recall(query_vec). Three query-dependent paths, and the block was
skipped entirely on a tool-free turn. Measured live 2026-07-27: skills_len went
4169 -> 0 across two turns of one conversation, the single largest remaining
source of prompt instability.

WHY "ALWAYS" RATHER THAN "LEAN ON CONVERSATIONAL TURNS". The old skip existed so
a chat turn did not carry needless playbook tokens — a real concern when the
block injected full bodies. A catalogue is names and descriptions only, so that
cost mostly evaporates; and D01.1's entire thesis is that a byte-identical prompt
is CHEAPER through automatic prefix caching than a per-turn-optimised one. A
block that disappears on some turns forfeits the cache on EVERY turn, which costs
more than the tokens it saves.

Depth is not lost: `skill_view` fetches a body when the model decides it needs
one, and slice 4a made that tool independent of the scoring this removes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import assemble

pytestmark = pytest.mark.asyncio


class _Skill:
    def __init__(self, name: str, description: str, body: str) -> None:
        self.name = name
        self.description = description
        self.when_to_use = f"when you need {name}"
        self.body_text = body
        self.source = "user"
        self.skill_id = 1
        self.enabled = True
        self.summary = ""


class _CatalogueStore:
    """Records which retrieval path assemble used."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._skills = [
            _Skill("pdf", "summarize pdfs", "BODY_MARKER: chunk and recurse"),
            _Skill("deploy", "ship a release", "BODY_MARKER: run the pipeline"),
        ]

    async def list_enabled(self):
        self.calls.append("list_enabled")
        return list(self._skills)

    async def hybrid_recall(self, query, vec, limit=10):  # pragma: no cover
        self.calls.append("hybrid_recall")
        return [(s, 1.0) for s in self._skills]

    async def semantic_recall(self, vec, limit=10):  # pragma: no cover
        self.calls.append("semantic_recall")
        return [(s, 1.0) for s in self._skills]

    async def get_many_by_name(self, names):
        return [s for s in self._skills if s.name in set(names)]


def _catalogue_of(prompt: str) -> str:
    """The skills region only — so a test about the CATALOGUE is not silently
    also asserting things about the charter around it."""
    marker = "- pdf:"
    i = prompt.find(marker)
    return prompt[i:] if i >= 0 else ""


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t-cat-1", session_key="owl:secretary:cli:dm:1", input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="assemble",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


class _ShellRegistry:
    """A tool registry with ``shell`` registered — the ONLY condition under which
    CapabilityManifest emits its device-access line.

    Without this, ``probe(tools_enabled=...)`` renders identically whichever way
    it is called, and any test asserting prompt equality across intent classes
    passes vacuously. That is exactly how the frozen-capability regression got
    through: the assertion was there, but nothing made it bite.
    """

    def get(self, name: str) -> object | None:
        return object() if name == "shell" else None


async def _prompt(store: _CatalogueStore, tmp_path: Path,
                  monkeypatch: pytest.MonkeyPatch, **kw: object) -> str:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    # global_catalog ON — the setting still governs whether skills the owl does
    # NOT own appear. Settings absent means OFF, which is pre-existing behaviour
    # this slice deliberately preserved rather than overriding.
    settings = SimpleNamespace(skills=SimpleNamespace(global_catalog=True))
    set_services(StepServices(  # type: ignore[arg-type]
        skill_store=store, settings=settings, tool_registry=_ShellRegistry(),
    ))
    out = await assemble.run(_state(**kw))
    return out.system_prompt or ""


async def test_two_different_questions_produce_the_same_skills_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this slice closes, and the last blocker on invariant I1."""
    store = _CatalogueStore()

    first = await _prompt(store, tmp_path, monkeypatch,
                          input_text="how do I deploy?", query_embedding=(0.1, 0.2))
    second = await _prompt(store, tmp_path, monkeypatch,
                           input_text="summarise this pdf", query_embedding=(0.9, 0.8))

    assert first == second


async def test_the_query_embedding_is_not_used_to_select_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stability has to come from not asking a query-shaped question, not from
    asking one and hoping the answer is stable."""
    store = _CatalogueStore()

    await _prompt(store, tmp_path, monkeypatch, query_embedding=(0.1, 0.2))

    assert "hybrid_recall" not in store.calls
    assert "semantic_recall" not in store.calls
    assert "list_enabled" in store.calls


async def test_names_and_descriptions_are_present_but_not_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q9 exactly: names + descriptions always, bodies on demand via skill_view."""
    store = _CatalogueStore()

    prompt = await _prompt(store, tmp_path, monkeypatch)

    assert "pdf" in prompt
    assert "summarize pdfs" in prompt
    assert "BODY_MARKER" not in prompt, "a full body must never be injected"


async def test_the_catalogue_is_present_on_a_tool_free_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"ALWAYS loaded" (Q9). The old block was skipped on conversational turns,
    which is why skills_len read 4169 then 0 across one conversation — a prompt
    that disappears on some turns forfeits the prefix cache on every turn."""
    store = _CatalogueStore()

    conversational = await _prompt(store, tmp_path, monkeypatch,
                                   intent_class="conversational")
    working = await _prompt(store, tmp_path, monkeypatch, intent_class="standard")

    # The catalogue is present on BOTH, and identical on both.
    for prompt in (conversational, working):
        assert "pdf" in prompt
        assert "summarize pdfs" in prompt
    assert _catalogue_of(conversational) == _catalogue_of(working)

    # WHOLE-PROMPT equality across intent classes — asserted, as of DEBT-22's
    # resolution. When this test was written it deliberately did NOT assert
    # this: `base` differed by 84 chars (3768 vs 3684 on 2026-07-27) because the
    # old build_base_prompt dropped the ACTION: protocol on a tool-free turn,
    # and that was then the LAST source of prompt variance.
    #
    # DEBT-22 chose "the protocol becomes unconditional". It is safe to make it
    # unconditional precisely because the imitation defence it existed for did
    # not go away — it moved to the VOLATILE tier, where "no capabilities are
    # available to you this turn" is delivered with the turn that is actually
    # tool-free, instead of being baked into a prompt frozen for the session.
    # A frozen prompt cannot express a per-turn conditional at all: a session
    # opening on a chat turn would otherwise carry a protocol-less prompt for
    # its whole life and lose tool use until the next rollover.
    assert conversational == working, (
        "the frozen system prompt must not vary with intent_class — DEBT-22"
    )

    # And the assertion BITES: the harness registers `shell`, so a per-turn
    # capability gate would show up here as a 243-char difference. See
    # test_device_access_survives_a_session_that_opens_on_a_chat_turn below.
    assert "direct access to this device" in conversational


async def test_device_access_survives_a_session_that_opens_on_a_chat_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION — introduced by the freeze itself, found in D01.1's cleanup.

    CapabilityManifest gates its device-access line on ``tools_enabled``, which
    assemble derived from THIS TURN's intent_class. That was correct while the
    prompt was rebuilt every turn. Once slice 5 froze the prompt for the life of
    a session, the same conditional became a session-long falsehood: a
    conversation whose first message is "hi" (a TOOL_FREE_CLASSES intent) froze
    a prompt missing the one line written to stop the owl claiming it is a
    remote cloud model that cannot reach the user's machine — and with daily
    rollover, it stayed missing for up to a day.

    Measured before the fix, with `shell` registered: 579 chars rendered with
    tools_enabled=True vs 336 with False.

    The fix is the same one DEBT-22 took for the call protocol, for the same
    reason: the banner asserts PLATFORM capability, which is a fact about the
    session, not the turn. The per-turn truth keeps its own home in
    ``volatile_turn_context(capabilities_offered=False)``.
    """
    store = _CatalogueStore()

    opened_on_chat = await _prompt(store, tmp_path, monkeypatch,
                                   intent_class="conversational")

    assert "direct access to this device" in opened_on_chat, (
        "a session opening on a chat turn must still freeze a prompt that "
        "states the platform's device access"
    )
    assert "never claim otherwise" in opened_on_chat
