"""D01.1 slice 4b — the skills block becomes a stable catalogue.

Bakir's Q9: "Names + descriptions ALWAYS loaded; full body fetched on demand via
tool call." The word that decides the shape is *always*.

WHAT WAS THERE. assemble scored owned skills against state.query_embedding
(score_owned_skills -> assign_tiers, deciding which got FULL bodies), then filled
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


async def _prompt(store: _CatalogueStore, tmp_path: Path,
                  monkeypatch: pytest.MonkeyPatch, **kw: object) -> str:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    # global_catalog ON — the setting still governs whether skills the owl does
    # NOT own appear. Settings absent means OFF, which is pre-existing behaviour
    # this slice deliberately preserved rather than overriding.
    settings = SimpleNamespace(skills=SimpleNamespace(global_catalog=True))
    set_services(StepServices(skill_store=store, settings=settings))  # type: ignore[arg-type]
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

    # NOT asserted: whole-prompt equality across intent classes. `base` still
    # differs, because build_base_prompt drops the ACTION: protocol on a
    # tool-free turn by design — teaching the calling protocol to a turn with
    # nothing to call made a weak model imitate it, traced live. That is the
    # 3768 -> 3684 base_len seen on 2026-07-27, and it is the LAST source of
    # prompt variance after this slice. It is a separate decision (should the
    # protocol stay conditional?) and is tracked as DEBT-22 rather than being
    # quietly folded into this one.
