"""ESC-9 — a profiled owl keeps the tools that make its profile usable.

THE BUG. A browser-profiled owl was offered FEWER browser tools than an
unprofiled one. Measured on ToolRegistry.with_defaults(): the full schema
presents 77 tools; the browser-profiled schema presented 36 and held only 18 of
the 26 browser tools. The eight dropped were browser_snapshot, browser_type,
browser_upload, browser_wait_for, the three browser_tab_* and browser_vision —
so the owl could click and navigate but could not SEE the page, type, upload,
wait, or manage tabs.

WHY THEY WERE THE ONES DROPPED, and why the fix is not "raise the budget".
`rank_candidates` orders discretionary tools by ``(-usage_score, name)``. With no
usage history every score is 0.0, so the order collapses to ALPHABETICAL and the
token budget cuts whatever sorts last. snapshot/tab/type/upload/vision/wait_for
are simply late in the alphabet. Nothing about them was judged less useful; they
just lost a tiebreak that was never meant to carry this weight.

THE CONSTRAINT THE FIX HAD TO RESPECT. D05.2 deliberately removed query-relevance
ranking because it made the presented array a function of the QUESTION, changing
every turn and defeating the position-0 prompt-cache marker D01.2 places (D01.3
measured 15 change events across 5 lane/owl pairs). So importance here must be
DECLARATIVE and stable — a property of the tool, not of the turn. A per-tool
``presentation_priority`` is exactly that: identical on every turn, for every
query, forever.
"""

from __future__ import annotations

import pytest

from stackowl.tools._infra.presentation import ToolPresentation


class _T:
    """Minimal tool double: name + manifest fields the ranker reads."""

    def __init__(self, name: str, group: str | None = None, priority: int = 0) -> None:
        self.name = name
        self._group = group
        self._priority = priority

    @property
    def manifest(self):  # type: ignore[no-untyped-def]
        from stackowl.tools.base import ToolManifest

        return ToolManifest(
            name=self.name,
            description=f"{self.name} does a thing",
            parameters={},
            toolset_group=self._group,
            presentation_priority=self._priority,
        )


class TestPriorityOrdersTheCut:
    def test_a_higher_priority_tool_ranks_ahead_of_an_alphabetically_earlier_one(
        self,
    ) -> None:
        """The whole bug in one assertion: "zzz" must be able to outrank "aaa"."""
        tools = [_T("aaa_tool", "grp"), _T("zzz_tool", "grp", priority=10)]

        _guaranteed, ranked = ToolPresentation().rank_candidates(
            all_tools=tools, profile=["grp"], pins=None, hydrated=None,
        )

        assert [t.name for t in ranked] == ["zzz_tool", "aaa_tool"]

    def test_equal_priority_still_falls_back_to_NAME_not_registry_order(self) -> None:
        """Determinism is the reason the name is always part of the key. Two tools
        on equal priority must not order by list position, or the presented array
        depends on registration order and the prompt-cache stability this whole
        design protects becomes luck."""
        tools = [_T("bbb", "grp", priority=5), _T("aaa", "grp", priority=5)]

        _g, ranked = ToolPresentation().rank_candidates(
            all_tools=tools, profile=["grp"], pins=None, hydrated=None,
        )

        assert [t.name for t in ranked] == ["aaa", "bbb"]

    def test_measured_usage_still_outranks_a_declared_priority(self) -> None:
        """Priority is a COLD-START default, not an override of evidence. What an
        owl actually uses is a stronger signal than what a tool author guessed."""
        tools = [_T("declared", "grp", priority=99), _T("used", "grp")]

        _g, ranked = ToolPresentation().rank_candidates(
            all_tools=tools, profile=["grp"], pins=None, hydrated=None,
            usage_scores={"used": 1.0},
        )

        assert [t.name for t in ranked] == ["used", "declared"]

    def test_the_order_does_not_depend_on_the_query(self) -> None:
        """D05.2's constraint, pinned. rank_candidates takes no request text at
        all, so this is structural — but asserting it stops a future change from
        quietly threading one in."""
        import inspect

        sig = inspect.signature(ToolPresentation.rank_candidates)
        assert not any(
            "request" in p or "query" in p or "text" in p for p in sig.parameters
        ), f"ranking must not become query-dependent again: {list(sig.parameters)}"


class TestTheBrowserProfileIsUsableAgain:
    def test_a_browser_profiled_owl_can_see_the_page_it_browses(self) -> None:
        """The user-visible outcome, against the REAL registry: an owl profiled for
        browser work gets snapshot (how it sees the page) and click (how it acts)."""
        from stackowl.tools.registry import ToolRegistry

        schema = ToolRegistry.with_defaults().to_provider_schema(
            "anthropic", profile=["browser"], pins=[],
        )
        names = {s["name"] for s in schema}

        assert "browser_snapshot" in names, sorted(n for n in names if n.startswith("browser_"))
        assert "browser_click" in names

    def test_the_core_browser_verbs_all_survive_the_budget(self) -> None:
        """Not just snapshot. A browser owl that cannot type or wait is still
        broken, in a way that would pass a snapshot-only assertion."""
        from stackowl.tools.registry import ToolRegistry

        schema = ToolRegistry.with_defaults().to_provider_schema(
            "anthropic", profile=["browser"], pins=[],
        )
        names = {s["name"] for s in schema}

        missing = [
            t for t in ("browser_snapshot", "browser_click", "browser_type",
                        "browser_browse", "browser_wait_for")
            if t not in names
        ]
        assert not missing, f"core browser verbs dropped by the budget: {missing}"


class TestDropsAreAnnounced:
    def test_evicted_tools_are_logged_not_silently_dropped(self) -> None:
        """Bakir's actual complaint was not that a budget exists — it is that the
        eviction was SILENT. An operator asking "why can't my owl type?" had
        nothing to read."""
        import logging

        from stackowl.tools.registry import ToolRegistry

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("stackowl.tool")
        handler = _Capture(level=logging.INFO)
        prev = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            ToolRegistry.with_defaults().to_provider_schema(
                "anthropic", profile=["browser"], pins=[],
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev)

        drops = [r for r in records if "not presented" in r.getMessage().lower()]
        assert drops, (
            "no INFO line reported the evicted tools; the drop is still silent"
        )
        named = getattr(drops[0], "_fields", {}).get("dropped")
        assert named, "the line must NAME the dropped tools — a count does not tell "\
                      "an operator which capability their owl lost"


pytestmark = pytest.mark.filterwarnings("ignore")
