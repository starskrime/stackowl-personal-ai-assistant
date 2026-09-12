"""What the platform remembers — and A05.5's gap named the wrong store. Three times.

The gap read: "CURATED memory is searchable by typing. The 237 facts in
`staged_facts` are reachable from no surface at all, and `committed_facts` — the
store a reader would expect — holds ZERO rows." Every clause is checkable and
each one points somewhere other than the answer:

* `committed_facts` is RETIRED, not broken. 0 rows since migration 0112, and
  `pipeline/state.py` records that its promotion path is "dead on both ends". A
  browser over it renders an empty retired table.
* `staged_facts` is SHORT-TERM CONVERSATION HISTORY — 178 raw turns, 57 rolling
  summaries, 2 agent-self. The same comment calls those rows "ONLY short-term
  history … the mirror that lets the agent know what it already told him".
  Rendering them as "what the platform remembers about you" MISREPRESENTS a
  transcript as knowledge. And they are not unreachable: the model's `memory`
  tool searches them. They are unreachable *by a person*.
* **CURATED MEMORY IS FILES, NOT A TABLE** — `~/.stackowl/memory/*.md`, read by
  `CuratedMemory`. MEASURED 2026-09-12: 19 files, 64 entries, of which `USER.md`
  holds 7. That is what an operator means by "see memories", and it is what
  `/memory search` already reads.
* And the gap never mentions `lessons` at all: **5,964 rows** — 5,689
  reflections, 218 skill, 57 tool-heuristic — written continuously, newest
  twenty minutes before this route existed, reachable from NO command and NO
  route.

SO THE ROUTE SHOWS THE CURATED ENTRIES AND THE LESSONS, AND COUNTS THE REST WITH
ITS KIND STATED. Merging them into one "memories" number is how a transcript
gets counted as knowledge, which is the single thing this surface must not do.

AND THE GAP WAS WRITTEN FROM DEAD CODE, which is the root cause rather than
carelessness. `memory_helpers._STATS_SQL` counted exactly the two stores the gap
names — `staged_facts` and `committed_facts` — and it had ZERO callers, as did
`collect_stats`, `format_stats`, `format_search_hits`,
`fetch_all_committed_for_reindex` and `RememberSourceType`. 145 lines describing
a memory model the platform retired in migration 0112, still readable, still the
most obvious thing to read. All deleted here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t0ken-for-tests-only"


class _Req:
    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


class _Db:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.seen: list[str] = []

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        self.seen.append(sql)
        for table, n in self._counts.items():
            if f"FROM {table}" in sql and "COUNT(*)" in sql:
                return [{"n": n}]
        if "GROUP BY source_type" in sql:
            return [{"source_type": "reflection", "n": 5689},
                    {"source_type": "skill", "n": 218}]
        if "FROM lessons" in sql:
            return [{
                "lesson_id": "l1", "source_type": "reflection",
                "source_ref": "trace-1", "content": "What worked for scout: …",
                "created_at": "2026-09-12T03:45:48Z",
            }]
        return []


def _server(db: Any = None) -> ControlPlaneServer:
    class _Cfg:
        bind_address = "127.0.0.1"
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    srv = ControlPlaneServer(_Settings(), db=db)  # type: ignore[arg-type]
    srv._token = _TOKEN  # noqa: SLF001
    return srv


def _body(res: Any) -> dict[str, Any]:
    return json.loads(res.text)


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server()._handle_memory(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server()._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}", Origin="http://evil.example",
                 Host="127.0.0.1:8787")
        )
        assert res.status == 401


class TestItShowsWhatIsActuallyRemembered:
    @pytest.mark.tripwire
    async def test_the_curated_entries_reach_the_response(self) -> None:
        """The operator's literal ask. `/memory search` already reads these and
        nothing showed them as a set."""
        db = _Db({"staged_facts": 237, "committed_facts": 0,
                  "learning_artifacts": 700})
        res = await _server(db)._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 200
        body = _body(res)
        targets = {t["target"] for t in body["curated"]}
        assert "user" in targets, (
            "the one target that is about the PERSON is missing — this surface "
            "answers 'what do you remember about me' or it answers nothing"
        )

    @pytest.mark.tripwire
    async def test_lessons_are_shown_WITH_their_denominator(self) -> None:
        """A window onto 5,964 rows read as the whole unless the totals are
        beside it."""
        db = _Db({"staged_facts": 237, "committed_facts": 0,
                  "learning_artifacts": 700})
        body = _body(await _server(db)._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        ))

        assert body["lessons"], "no lessons rendered at all"
        assert body["counts_by_source"] == {"reflection": 5689, "skill": 218}
        assert body["total"] == 5907

    @pytest.mark.tripwire
    async def test_every_other_store_states_WHAT_KIND_it_is(self) -> None:
        """`staged_facts` is a transcript. A surface that lists it beside curated
        entries under one heading turns conversation history into knowledge."""
        db = _Db({"staged_facts": 237, "committed_facts": 0,
                  "learning_artifacts": 700})
        body = _body(await _server(db)._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        ))

        by_store = {s["store"]: s for s in body["other_stores"]}
        assert by_store["staged_facts"]["kind"] == "short-term history"
        assert by_store["committed_facts"]["kind"] == "retired"
        assert by_store["staged_facts"]["rows"] == 237

    @pytest.mark.tripwire
    async def test_there_is_no_single_merged_memories_number(self) -> None:
        """The one thing this surface must not do."""
        db = _Db({"staged_facts": 237, "committed_facts": 0,
                  "learning_artifacts": 700})
        body = _body(await _server(db)._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        ))

        assert "memories" not in body, (
            "a merged count appeared — it adds a transcript to curated notes and "
            "calls the sum knowledge"
        )

    @pytest.mark.tripwire
    async def test_an_unwired_db_CONFESSES_rather_than_returning_empty(self) -> None:
        res = await _server(None)._handle_memory(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 503
        assert _body(res) == {"lessons": [], "wired": False}


class TestTheDeadMemoryMachineryIsGone:
    @pytest.mark.tripwire
    def test_the_retired_stats_helpers_no_longer_exist(self) -> None:
        """They counted exactly the two stores A05.5's gap named, and had ZERO
        callers. 145 lines describing a memory model migration 0112 retired —
        still readable, and the most obvious thing to read."""
        from stackowl.commands import memory_helpers as mod

        for gone in ("collect_stats", "format_stats", "format_search_hits",
                     "fetch_all_committed_for_reindex", "_STATS_SQL",
                     "RememberSourceType"):
            assert not hasattr(mod, gone), f"{gone} survived the retirement"

    @pytest.mark.tripwire
    def test_what_the_module_still_does_is_untouched(self) -> None:
        """The vacuity control: a deletion that took the live half with it would
        pass the test above."""
        from stackowl.commands import memory_helpers as mod

        for kept in ("forget_fact", "do_export", "parse_export_args",
                     "format_budget"):
            assert hasattr(mod, kept), f"{kept} was deleted with the dead half"


class TestThePageShowsBoth:
    @pytest.mark.tripwire
    def test_the_panel_separates_curated_notes_from_learned_lessons(self) -> None:
        from stackowl.control_plane.page import INDEX_HTML

        assert 'id="curatedrows"' in INDEX_HTML
        assert 'id="lessonrows"' in INDEX_HTML
        assert "Learned from doing the work" in INDEX_HTML


class TestTheOtherStoresAreOwnerScopedAndTheGuardCanSEEThat:
    """The three counted stores are owner-governed, and this route would have
    read them unscoped through a hole in the guard that forbids exactly that.

    `tests/tenancy/test_no_owner_scope_bypass.py` finds a bypass by walking
    `ast.Constant` string literals, so a relation name arriving as an f-string
    INTERPOLATION is invisible to it — MEASURED 2026-09-12, 21 such statements
    already in `src/`. The first draft of `_other_memory_counts` looped over
    table names building `f"... FROM {table}"`, which is the 22nd, written by the
    same change that DELETES two of that guard's allowlist entries.

    AND LIVE DATA COULD NOT HAVE CAUGHT IT EITHER: every row in all three tables
    belongs to `principal-default` (237 / 0 / 700, measured), so a scoped and an
    unscoped count are the same number on this box. Both halves are pinned here —
    the behaviour against a CONSTRUCTED second owner, and the structural form
    that makes the rule able to read these statements at all.
    """

    @pytest.mark.tripwire
    async def test_a_second_owners_rows_are_not_counted_as_mine(self) -> None:
        """The constructed population. A single-principal install cannot tell a
        scoped read from an unscoped one, so the population is built here."""
        from stackowl.memory import activity as mod

        class _TwoOwnerDb:
            async def fetch_all(
                self, sql: str, params: Any = ()
            ) -> list[dict[str, Any]]:
                assert "owner_id = ?" in sql, f"unscoped read of a governed table: {sql}"
                owner = params[0]
                # 7 rows are mine; 11 belong to somebody else entirely.
                return [{"n": 7 if owner == "principal-default" else 11}]

        out = await mod.read_other_memory_counts(
            _TwoOwnerDb(), owner_id="principal-default"
        )

        assert [o.rows for o in out] == [7, 7, 7], (
            f"a count crossed the owner fence: {[(o.store, o.rows) for o in out]}"
        )

    @pytest.mark.tripwire
    def test_each_statement_names_its_table_as_a_LITERAL(self) -> None:
        """The structural half. A statement the detector cannot read is not
        exempt from the rule — it is invisible to it, which is worse."""
        import ast
        import inspect

        from stackowl.memory import activity as mod

        literals = {
            node.value
            for node in ast.walk(ast.parse(inspect.getsource(mod)))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for table in ("staged_facts", "committed_facts", "learning_artifacts"):
            matching = [
                s for s in literals
                if f"FROM {table}" in s and "COUNT(*)" in s
            ]
            assert matching, (
                f"no plain string literal counts `{table}` — if it is built by an "
                f"f-string, the owner-scope detector cannot see it at all"
            )
            for s in matching:
                assert "owner_id" in s, f"unscoped: {s}"
