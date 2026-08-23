"""ESC-36 — when an owl has no evidence for a tool, fall back to the PLATFORM's
evidence, not to the dictionary.

MEASURED 2026-08-23 over the 30-day window (9,349 task_outcomes rows):

* 15 of 18 owls have usable usage history; only 3 are truly cold-start.
* But that is NOT where the damage is. `headhunter` has history for **14 distinct
  tools out of 77**; `secretary` for 23. Every remaining tool scores 0, ties, and
  the key `(-usage, -declared_priority, name)` collapses to its last term — so
  which of the ~60 unscored tools fill the remaining cap slots is decided by
  SPELLING. That applies to all 15 owls WITH history, not merely the 3 without.
* `presentation_priority` cannot rescue them: it is declared on 8 of 77 tools and
  all eight are browser tools.

The platform's own global usage is the obvious signal and it is already recorded
in the same rows — web_search 3,506 dispatches, web_fetch 1,132, memory 408,
shell 283. Ordering unscored tools by that is data-derived, not hand-ranked, which
is what makes it maintainable: a curated ranking of 77 tools is a constant that
rots, exactly as the eight lockstep cap bumps did.

DETERMINISM IS PRESERVED, and that is not incidental. `name` remains the FINAL
term, so the ordering is still total and the presented array is still byte-stable
within a session — the property D01.2's position-0 cache marker depends on. This
is the same argument as ESC-44 for skills: a better tie-break, not a less stable
one.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS, ToolPresentation


class _T:
    """Tool double built from the REAL ToolManifest.

    My first attempt was a two-attribute stub and it exploded on
    `tool.manifest.requires_capability` — the capability gate reads more of a
    Tool than the ranker does. That is defect shape 2 in miniature, caught by the
    code rather than by review, so the double is now constructed from the same
    class production uses. Mirrors the double in test_presentation_priority.py.
    """

    def __init__(self, name: str, priority: int = 0) -> None:
        self.name = name
        self._priority = priority

    @property
    def manifest(self):  # type: ignore[no-untyped-def]
        from stackowl.tools.base import ToolManifest

        return ToolManifest(
            name=self.name,
            description=f"{self.name} does a thing",
            parameters={},
            toolset_group="grp",
            presentation_priority=self._priority,
        )


def _rank(tools: list[_T], **kw: object) -> list[str]:
    _guaranteed, ranked = ToolPresentation().rank_candidates(  # type: ignore[arg-type]
        all_tools=tools, profile=["grp"], pins=None, hydrated=None,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )
    return [t.name for t in ranked]


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_global_usage_beats_the_alphabet_for_unscored_tools() -> None:
    """`zzz_hot` is used constantly platform-wide; `aaa_cold` never. Spelling
    would present the wrong one."""
    tools = [_T("aaa_cold"), _T("zzz_hot")]
    order = _rank(tools, usage_scores={}, global_usage_scores={"zzz_hot": 500.0})
    assert order[0] == "zzz_hot", order


def test_the_owls_OWN_evidence_still_wins_over_the_platforms() -> None:
    """An owl that actually uses a tool must not be overruled by global habit.
    This is the ordering ESC-9 established and it is unchanged."""
    tools = [_T("mine"), _T("everyones")]
    order = _rank(
        tools,
        usage_scores={"mine": 1.0},
        global_usage_scores={"everyones": 9999.0},
    )
    assert order[0] == "mine", order


def test_declared_priority_still_outranks_global_usage() -> None:
    """ESC-9's middle term keeps its place — a browser owl must not lose the
    tools that let it see and type just because the platform mostly websearches."""
    tools = [_T("browser_snapshot", priority=100), _T("web_search")]
    order = _rank(
        tools, usage_scores={}, global_usage_scores={"web_search": 3506.0}
    )
    assert order[0] == "browser_snapshot", order


# ---------------------------------------------------------------------------
# Law 1 — the ordering must stay total and stable
# ---------------------------------------------------------------------------

def test_name_is_still_the_final_term_so_the_order_is_total() -> None:
    tools = [_T("b"), _T("a"), _T("c")]
    order = _rank(tools, usage_scores={}, global_usage_scores={})
    assert order == ["a", "b", "c"], "with no evidence at all, spelling is fine"


def test_the_order_does_not_depend_on_input_order() -> None:
    g = {"x": 5.0, "y": 5.0}
    a = _rank([_T("x"), _T("y"), _T("z")], usage_scores={}, global_usage_scores=g)
    b = _rank([_T("z"), _T("y"), _T("x")], usage_scores={}, global_usage_scores=g)
    assert a == b


def test_repeated_ranks_are_identical() -> None:
    tools = [_T(f"t{i}") for i in range(20)]
    g = {"t7": 3.0, "t13": 9.0}
    first = _rank(tools, usage_scores={}, global_usage_scores=g)
    for _ in range(4):
        assert _rank(tools, usage_scores={}, global_usage_scores=g) == first


def test_omitting_global_scores_entirely_still_works() -> None:
    """Every existing caller passes no global scores; none may break."""
    assert _rank([_T("b"), _T("a")], usage_scores={}) == ["a", "b"]


# ---------------------------------------------------------------------------
# clarify joins the guaranteed floor
# ---------------------------------------------------------------------------

def test_clarify_is_guaranteed() -> None:
    """ESC-36's sharpest instance. APPEAL_TOOLS guarantees {owl_build, owls_list}
    under "an agent must always be able to ASK for what it lacks" — so asking for
    a new OWL was guaranteed while asking the USER was discretionary and got
    evicted by spelling. Same principle, one addressee over."""
    assert "clarify" in _DEFAULT_ALWAYS


def test_the_appeal_tools_it_sits_beside_are_still_guaranteed() -> None:
    for name in ("owl_build", "owls_list", "tool_search", "tool_describe"):
        assert name in _DEFAULT_ALWAYS, name
