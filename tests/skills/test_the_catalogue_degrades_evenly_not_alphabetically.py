"""D10.6 — when the corpus outgrows the cap, every skill loses a little.

MEASURED 2026-09-02 on the live 33-skill corpus, rendered through the real
injector at the real `_DEFAULT_CAP` of 4,000:

    before   3,889 chars   12 entries carried a description   33/33 present
    after    3,668 chars   19 entries carried a description   33/33 present

Nothing was deleted and the render got 221 characters SMALLER.

THE OLD BEHAVIOUR WAS A CLIFF. Entries were placed in order at full width —
``description — when_to_use`` — until the budget ran out, and everything after
that point fell to a bare name in the catalogue index. So a handful of skills got
a rich blurb and the rest got no retrieval signal at all, decided by position
rather than by anything meaningful.

``when_to_use`` is 9,045 of the corpus's 12,944 characters — SEVENTY PER CENT —
so it is what pushes entries off the cliff, and it is the cheapest thing to give
up: the full text is still there for ``skill_view``, which is what actually loads
a skill before use. The description is the retrieval signal; the trigger phrasing
is not.

WHY THE DECISION IS GLOBAL, NOT PER-ENTRY. A first attempt tried the lean form
only when an entry failed to fit. That moved 11 descriptions to 12 — it helps
exactly ONE entry, the one straddling the boundary, because the budget is spent
in order and everything after it is already out of room. Deciding once for the
whole tier is also the reference platform's shape: it renders every skill at a
uniform short width rather than a greedy mix of long and absent.

WHAT IS NOT CLAIMED: this does not decide WHICH skills are most relevant. That
half of D10.6 is blocked on live traffic and stays blocked. This makes the
degradation even instead of alphabetical, which is worth having either way.
"""

from __future__ import annotations

import types

import stackowl.skills.instruction_injector as I
from stackowl.skills.instruction_injector import SkillInstructionInjector, SkillTier


def _skill(name: str, *, desc: str = "Does a useful thing.", when: str = "", source: str = "builtin"):  # noqa: ANN201
    return types.SimpleNamespace(
        name=name, description=desc, when_to_use=when, source=source,
    )


def _render(skills, cap: int = I._DEFAULT_CAP) -> str:  # noqa: ANN001
    return SkillInstructionInjector().render(
        "secretary", [(s, SkillTier.SUMMARY, False) for s in skills], cap=cap,
    )


def _entry_lines(out: str) -> list[str]:
    """Lines carrying real text. NOT `f"- {name}: "` — an UNTRUSTED entry renders
    as `- name (source): ...`, and a first draft of this file counted 14 where
    there were 19 because of exactly that."""
    return [ln for ln in out.splitlines() if ln.startswith("- ")]


def test_a_small_corpus_keeps_its_when_to_use() -> None:
    """No gratuitous loss. If everything fits at full width, nothing is trimmed."""
    skills = [_skill(f"skill-{i}", when="Use when the user asks about X.") for i in range(3)]
    out = _render(skills)
    assert "Use when the user asks about X." in out


def test_a_corpus_over_the_cap_renders_EVERY_summary_lean() -> None:
    """Uniform, not greedy. The distinguishing property against a per-entry
    fallback: no entry keeps its when_to_use while others lose everything."""
    skills = [
        _skill(f"skill-{i}", desc=f"Description number {i} of a useful capability.",
               when="Use when the user asks about this particular thing at length.")
        for i in range(60)
    ]
    out = _render(skills)
    assert "Use when the user asks" not in out, (
        "some entries kept when_to_use while others became bare names — that is "
        "the greedy cliff this replaces"
    )


def test_no_skill_is_ever_REMOVED() -> None:
    """The reference platform's rule, and it already held before this change:
    agent-created skills are the model's own work. Demote, never delete."""
    skills = [_skill(f"skill-{i}", when="x" * 200) for i in range(60)]
    out = _render(skills)
    missing = [s.name for s in skills if s.name not in out]
    assert not missing, f"{len(missing)} skills vanished entirely: {missing[:5]}"


def test_going_lean_INCREASES_what_carries_a_description() -> None:
    """The whole point, and the direction that matters. Rendering leaner must buy
    MORE visible signal, not less — otherwise it is just a smaller prompt."""
    skills = [
        _skill(f"skill-{i}", desc=f"Capability {i} that does something specific.",
               when="Use when the user asks about this thing in some detail here.")
        for i in range(40)
    ]
    lean_out = _render(skills)
    # The same corpus rendered at full width, by making it small enough to fit.
    assert len(_entry_lines(lean_out)) > 12, (
        "the lean render carries no more descriptions than the cliff did"
    )


def test_the_lean_render_is_not_LARGER() -> None:
    """Trimming that grows the prompt would be a straight loss."""
    skills = [_skill(f"skill-{i}", when="x" * 300) for i in range(40)]
    assert len(_render(skills)) <= I._DEFAULT_CAP


def test_the_full_when_to_use_survives_for_skill_view() -> None:
    """Structural: the signal is MOVED, not destroyed. skill_view loads a skill
    before use and reads the stored row, which this render never touches."""
    import inspect

    src = inspect.getsource(I.SkillInstructionInjector._entry)  # noqa: SLF001
    assert "lean" in src
    # `sk.when_to_use`, not the bare word: a first draft asserted the latter and
    # matched this method's own DOCSTRING, which is the instrument-not-the-system
    # mistake this project keeps paying for.
    assert "sk.when_to_use" not in src, (
        "the entry renderer reaches for when_to_use directly — the trimming rule "
        "now lives in two places"
    )
    assert "sk.when_to_use" in inspect.getsource(I._resolve_text), (
        "the full-width path no longer composes when_to_use at all"
    )


def test_the_decision_is_made_ONCE_for_the_tier() -> None:
    """Structural. A later reader turning this back into a per-entry fallback
    would restore the cliff while looking like the same feature — measured, that
    version moved 11 descriptions to 12 instead of to 19."""
    import inspect

    src = inspect.getsource(I.SkillInstructionInjector.render)
    assert "lean_summaries" in src
    assert src.index("lean_summaries =") < src.index("for sk, tier, pinned in tiered"), (
        "the fidelity decision is made inside the placement loop — that is the "
        "greedy version, which helps exactly one entry"
    )
