"""ESC-44 — the injected skill catalogue is ordered by VALUE, not by spelling.

MEASURED 2026-08-23, which is what made this a decision rather than a preference.
The catalogue is capped at 4,000 characters while 160 skills are enabled, so it
truncates on essentially every turn — 2,460 ``skill injection: catalog truncated
by budget`` records across the retained window, carrying ``dropped`` of 146-149.
Reproducing the render against the live store put the visible set at roughly a
dozen skills, cut alphabetically around the letter "c".

The selection was ANTI-CORRELATED with value. Skills inside the visible window
accounted for ~18 executions; the invisible tail accounted for ~199. About 92% of
all measured skill usage was invisible to the model on every turn, and the four
most-used skills in the corpus (the ``structure-incident-evidence`` family, 45 /
43 / 23 / 13 runs) all begin with "s" and were dropped every time.

WHY IT WAS ALPHABETICAL, AND WHY THAT REASON SURVIVES. The original sort was
deliberate — assemble.py's own comment says the block must be byte-identical
across turns, which is Law 1, prompt caching. That requirement is untouched here:
byte-identity needs a DETERMINISTIC key, not an alphabetical one. Sorting by
``(n_executions desc, lifecycle_state, name)`` is equally deterministic — the same
guarantee with a better tie-break — and ``name`` remains the final term so the
order is TOTAL and can never depend on the order rows came back from SQLite.

That property is the one these tests guard hardest: a value-ordered catalogue
that was not perfectly stable would defeat the caching it was careful to keep.
"""

from __future__ import annotations

from stackowl.skills.store import Skill, catalogue_order_key


def _sk(name: str, runs: int = 0, state: str = "active") -> Skill:
    return Skill(
        skill_id=abs(hash(name)) % 100000,
        name=name,
        source="learned",
        path=f"/tmp/{name}",
        description="d",
        when_to_use="w",
        version="0.1.0",
        enabled=True,
        success_rate=None,
        n_executions=runs,
        parent_traces=[],
        embedding=None,
        embedding_model=None,
        tool_names=(),
        body_text="",
        manifest_json={},
        loaded_at=0.0,
        updated_at=0.0,
        lifecycle_state=state,
    )


def _order(skills: list[Skill]) -> list[str]:
    return [s.name for s in sorted(skills, key=catalogue_order_key)]


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_used_skill_outranks_an_unused_one_regardless_of_spelling() -> None:
    """The exact live shape: `structure-incident-evidence` (45 runs, sorts last)
    vs `ai-brainstorm-daily` (0 runs, sorts first)."""
    order = _order([
        _sk("ai-brainstorm-daily", runs=0),
        _sk("structure-incident-evidence", runs=45),
    ])
    assert order[0] == "structure-incident-evidence", order


def test_the_measured_top_four_all_precede_the_measured_visible_set() -> None:
    """Reproduces the real inversion rather than a toy one."""
    corpus = [
        _sk("structure-incident-evidence", runs=45),
        _sk("structure-incident-evidence-brief", runs=43),
        _sk("structure-evidence-brief", runs=23),
        _sk("evidence-brief-structuring", runs=13),
        # what used to occupy the visible slots, all never executed
        _sk("ai-brainstorm-daily", runs=0),
        _sk("ai-news-briefing", runs=0),
        _sk("anthropic-interview-coach", runs=0),
        _sk("avoid_shell_for_web_fetching", runs=0),
    ]
    order = _order(corpus)
    assert order[:4] == [
        "structure-incident-evidence",
        "structure-incident-evidence-brief",
        "structure-evidence-brief",
        "evidence-brief-structuring",
    ], order


def test_active_beats_stale_at_equal_usage() -> None:
    """92 stale skills were competing on equal terms, because the only stale
    penalty in the tree lives inside `hybrid_recall`, which has zero callers."""
    order = _order([
        _sk("b-stale", runs=3, state="stale"),
        _sk("a-active", runs=3, state="active"),
    ])
    assert order[0] == "a-active", order


# ---------------------------------------------------------------------------
# Law 1: the ordering must stay perfectly deterministic
# ---------------------------------------------------------------------------

def test_the_order_is_total_and_independent_of_input_order() -> None:
    """A byte-identical prompt needs a TOTAL order. If two skills could ever
    compare equal, the block would depend on SQLite's row order and the cache
    would miss at random — the exact failure the alphabetical sort avoided."""
    corpus = [
        _sk("alpha", runs=5), _sk("beta", runs=5), _sk("gamma", runs=0),
        _sk("delta", runs=0, state="stale"), _sk("epsilon", runs=99),
    ]
    forward = _order(corpus)
    backward = _order(list(reversed(corpus)))
    shuffled = _order([corpus[i] for i in (3, 0, 4, 2, 1)])
    assert forward == backward == shuffled, (forward, backward, shuffled)


def test_identical_names_never_collide_on_the_key() -> None:
    """`name` is the final term, so the key is unique whenever names are."""
    keys = {
        catalogue_order_key(_sk(n, runs=r, state=s))
        for n, r, s in [
            ("a", 0, "active"), ("b", 0, "active"),
            ("a", 1, "active"), ("a", 0, "stale"),
        ]
    }
    assert len(keys) == 4


def test_repeated_sorts_of_the_same_corpus_are_byte_stable() -> None:
    corpus = [_sk(f"s-{i}", runs=i % 7, state="stale" if i % 3 else "active")
              for i in range(40)]
    first = _order(corpus)
    for _ in range(5):
        assert _order(corpus) == first


# ---------------------------------------------------------------------------
# It must not crash on rows that predate the columns
# ---------------------------------------------------------------------------

def test_a_missing_or_odd_lifecycle_state_still_sorts() -> None:
    order = _order([
        _sk("z-unknown-state", runs=1, state="something-new"),
        _sk("a-active", runs=1, state="active"),
    ])
    assert order[0] == "a-active", "a known-good state must outrank an unknown one"


def test_zero_usage_across_the_board_falls_back_to_the_old_behaviour() -> None:
    """With no usage signal anywhere, the order is the alphabetical one it always
    was — so nothing regresses on a fresh install."""
    names = ["charlie", "alpha", "bravo"]
    assert _order([_sk(n) for n in names]) == ["alpha", "bravo", "charlie"]
