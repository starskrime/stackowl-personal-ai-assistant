"""How the agents interact — and A05.7's gap named the wrong mechanism.

The gap read: "Delegation, parliament and owl-to-owl messages happen inside
mailboxes nobody can watch, so multi-agent behaviour can only be reconstructed
from logs after the fact." MEASURED 2026-09-12 against the tree and the live
database, three of its four clauses are wrong:

* **The edges ARE recorded**, in two durable stores: `tasks.parent_task_id`
  (52 rows) and `side_effect_ledger` `tool_name='delegate_task'` (17 rows, each
  carrying `to_owl` and an outcome inside `result_blob`). The gap's own evidence
  line said so — "the edges already exist as data" — and the sentence above it
  contradicted that.
* **"Only from logs" is true of the RECENT ones only.** The retained logs hold
  SEVEN successful cross-owl delegations (`delegate_task.execute: exit`,
  `status: ok`), newest 2026-09-11; the durable ledger holds TWO. The three on
  2026-09-11 each logged `delegate_task: non-durable parent — this delegation is
  PROCESS-LOCAL and will not survive a restart`, which is why they left no row.
* **Parliament is not unwatchable — it has never run.** `parliament_sessions`
  holds 0 rows and `[parliament]` appears 0 times across every retained log,
  while `ParliamentOrchestrator` is constructed at boot. A panel over it renders
  empty forever, so it is reported as a COUNT with its nature stated.
* **The clause that HOLDS** is the one about mailboxes, and it is about the
  message HOP rather than the edge: `A2AQueue` is `dict[str, asyncio.Queue]`,
  in-memory, and 14 of its log calls are DEBUG against 0 DEBUG records.

AND THE REAL GAP, which `gap_check` confirms: **no command and no control-plane
route has ever read `tasks.parent_task_id`.** The data is there and unreachable —
the seventh instance of that class in this series.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t" * 43


class _Req:
    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


def _blob(record: dict[str, Any] | None, *, output: object = None) -> str:
    """A `side_effect_ledger.result_blob`, in the real nested shape.

    JSON INSIDE JSON: the outer object carries `output` as a serialised STRING.
    Tests that build the flat shape would pass against a reader that cannot read
    the real one, which is the fixture-stopped-resembling-the-real-thing defect
    this repo names as one of its six recurring shapes.
    """
    if output is None:
        output = json.dumps({"note": "n", "record": record}) if record is not None else ""
    return json.dumps({"success": True, "output": output})


class _Db:
    def __init__(self, decomp: list[dict[str, Any]], deleg: list[dict[str, Any]],
                 *, unseen: int = 0, parliament: int = 0) -> None:
        self.decomp, self.deleg = decomp, deleg
        self.unseen, self.parliament = unseen, parliament
        self.seen: list[tuple[str, Any]] = []

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        self.seen.append((sql, params))
        if "parliament_sessions" in sql:
            return [{"n": self.parliament}]
        if "owner_id <> ?" in sql:
            return [{"n": self.unseen}]
        if "side_effect_ledger" in sql:
            return list(self.deleg)
        return list(self.decomp)


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


def _authorised() -> _Req:
    return _Req(Authorization=f"Bearer {_TOKEN}")


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server()._handle_interactions(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server()._handle_interactions(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}", Origin="http://evil.example",
                 Host="127.0.0.1:8787")
        )
        assert res.status == 401


class TestTheTwoKindsAreNeverSummed:
    @pytest.mark.tripwire
    async def test_a_self_decomposition_is_not_reported_as_a_delegation(self) -> None:
        """52 of the 52 live `tasks` edges are an owl decomposing its OWN work.
        Labelling those "interactions" reports a busy multi-agent platform that
        is one owl talking to itself."""
        db = _Db(
            decomp=[{
                "task_id": "c1", "child_owl": "secretary", "status": "completed",
                "created_at": "2026-09-11T15:26:09+00:00", "goal": "step four",
                "trigger_kind": "subgoal", "parent_owl_actual": "secretary",
            }],
            deleg=[],
        )
        payload = _body(await _server(db)._handle_interactions(_authorised()))  # noqa: SLF001

        assert [e["kind"] for e in payload["edges"]] == ["decomposition"]
        assert "interactions" not in payload, (
            "a single merged count would hide that every edge is self-decomposition"
        )

    @pytest.mark.tripwire
    async def test_a_cross_owl_delegation_carries_both_ends_and_its_outcome(self) -> None:
        db = _Db(
            decomp=[],
            deleg=[{
                "task_id": "task-27fac0738d78", "status": "committed",
                "created_at": "2026-08-24T22:02:14+00:00", "from_owl": "secretary",
                "result_blob": _blob({"status": "ok", "to_owl": "archivist"}),
            }],
        )
        edges = _body(await _server(db)._handle_interactions(_authorised()))["edges"]  # noqa: SLF001

        assert edges == [{
            "kind": "delegation", "from_owl": "secretary", "to_owl": "archivist",
            "outcome": "ok", "at": "2026-08-24T22:02:14+00:00",
            "ref": "task-27fac0738d78", "detail": "n",
        }]


class TestATimeoutIsNotTheSameAsAnUnREADABLEROW:
    """THE CONSTRUCTED POPULATION, and it caught a real conflation.

    The first draft reported `timeout` whenever the record came back empty,
    which swept a row the reader could not parse into the same bucket as a real
    timeout — the live data went from 4 timeouts / 0 unreadable to the true 3/1
    when the two were separated. An outcome that cannot tell "the delegation
    timed out" from "I could not read this row" is unfalsifiable, and only a
    population built here can pin the difference: on the live tree the two
    counts move together often enough to look correct.
    """

    @pytest.mark.tripwire
    async def test_an_EMPTY_output_is_a_timeout(self) -> None:
        """`A2ADelegator.delegate` returns `""` on timeout — its own docstring
        says so — so an `output` key present and empty is a real outcome."""
        db = _Db(decomp=[], deleg=[{
            "task_id": "t", "status": "committed", "created_at": "2026-08-19T17:00:40+00:00",
            "from_owl": None, "result_blob": _blob(None, output=""),
        }])
        edges = _body(await _server(db)._handle_interactions(_authorised()))["edges"]  # noqa: SLF001

        assert edges[0]["outcome"] == "timeout"

    @pytest.mark.tripwire
    async def test_a_row_this_reader_CANNOT_parse_says_so(self) -> None:
        db = _Db(decomp=[], deleg=[{
            "task_id": "t", "status": "committed", "created_at": "2026-08-19T17:00:40+00:00",
            "from_owl": None, "result_blob": "{not json at all",
        }])
        edges = _body(await _server(db)._handle_interactions(_authorised()))["edges"]  # noqa: SLF001

        assert edges[0]["outcome"] == "unreadable", (
            "an unparseable row reported as `timeout` is a claim about the "
            "platform made from a failure of this reader"
        )


class TestTheDenominatorIsStated:
    @pytest.mark.tripwire
    async def test_an_edge_missing_an_end_is_COUNTED_not_hidden(self) -> None:
        """14 of the 17 live delegation rows have no recoverable caller — the
        calling `tasks` row is gone. Dropping them would under-report the edges;
        drawing an arrow from nowhere would invent one."""
        db = _Db(decomp=[], deleg=[{
            "task_id": "gone", "status": "committed", "created_at": "2026-08-21T02:11:00+00:00",
            "from_owl": None, "result_blob": _blob({"status": "irrelevant"}),
        }])
        payload = _body(await _server(db)._handle_interactions(_authorised()))  # noqa: SLF001

        assert len(payload["edges"]) == 1
        assert payload["gaps"]["delegations_without_a_caller"] == 1
        assert payload["gaps"]["delegations_without_a_target"] == 1

    @pytest.mark.tripwire
    async def test_parliament_is_a_COUNT_rather_than_an_empty_panel(self) -> None:
        """0 rows and 0 log lines across every retained log, while wired at
        boot. A table that renders empty forever is the `committed_facts` shape;
        a count that says zero is information."""
        db = _Db(decomp=[], deleg=[], parliament=0)
        payload = _body(await _server(db)._handle_interactions(_authorised()))  # noqa: SLF001

        assert payload["gaps"]["parliament_sessions"] == 0
        from stackowl.control_plane.page import INDEX_HTML

        assert 'id="parliament"' not in INDEX_HTML, (
            "parliament has never run — a panel over it renders empty forever"
        )


class TestItReadsBothGovernedTablesWithinTheOwnerFence:
    @pytest.mark.tripwire
    async def test_every_statement_is_owner_scoped(self) -> None:
        """`tasks` and `side_effect_ledger` are BOTH owner-governed, measured
        against `_OWNER_GOVERNED_TABLES` rather than assumed."""
        db = _Db(decomp=[], deleg=[])
        await _server(db)._handle_interactions(_authorised())  # noqa: SLF001

        reads = [sql for sql, _ in db.seen if "SELECT" in sql]
        assert reads, "the reader issued no statement — this guard has gone blind"
        for sql in reads:
            assert "owner_id" in sql, f"unscoped read of a governed table: {sql}"

    @pytest.mark.tripwire
    def test_each_statement_names_its_table_as_a_LITERAL(self) -> None:
        """A relation arriving as an f-string interpolation is INVISIBLE to
        `test_no_owner_scope_bypass`, which walks `ast.Constant`. A05.5 measured
        21 such statements already in `src/`; this one adds none."""
        import ast
        import inspect

        from stackowl.pipeline.durable import interactions as mod

        literals = {
            n.value
            for n in ast.walk(ast.parse(inspect.getsource(mod)))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for table in ("side_effect_ledger", "parliament_sessions"):
            assert any(table in s and "SELECT" in s for s in literals), (
                f"no plain literal reads `{table}` — if it is built by an "
                f"f-string the owner-scope detector cannot see it at all"
            )


class TestThePageShowsIt:
    @pytest.mark.tripwire
    def test_the_panel_renders_the_edges_and_their_denominator(self) -> None:
        from stackowl.control_plane.page import INDEX_HTML

        assert 'id="edgerows"' in INDEX_HTML
        assert 'id="edgenote"' in INDEX_HTML
        assert "How the agents interact" in INDEX_HTML
