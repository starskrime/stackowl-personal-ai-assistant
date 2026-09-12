"""`GET /api/v1/tasks` — the set view A05.6's gap says does not exist.

The reader and its `blocked` reasoning are proven in
`tests/pipeline/durable/test_work_in_flight_is_readable_as_a_set.py`, against a
real pool. This file proves the DOOR: that the route is locked like every other
data route, that it presents what the reader returns without inventing a shape,
and that it confesses rather than returning an empty list when it cannot look.
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
    """Asserts the owner predicate survives, in the double rather than only in a
    tripwire that may not be derived (DEBT-292)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.seen: list[str] = []

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        assert "owner_id" in sql, f"a statement lost its owner predicate: {sql}"
        self.seen.append(sql)
        if "COUNT(*)" in sql:
            return [{"n": 3}]
        return self._rows


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


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_id": "t1", "owl_name": "secretary", "status": "pending",
        "attempt_count": 2, "max_attempts": 30,
        "next_attempt_at": "2026-09-11T14:36:56+00:00",
        "lease_owner": None, "lease_expires_at": None,
        "goal": "do the thing", "last_error": "ran out of tokens",
        "superseded": 0, "parent_task_id": "p1", "depends_on": None,
        "sql_eligible": 0, "parent_status": "completed",
    }
    base.update(over)
    return base


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server()._handle_tasks(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server()._handle_tasks(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}", Origin="http://evil.example",
                 Host="127.0.0.1:8787")
        )
        assert res.status == 401


class TestItServesTheSetWithItsReason:
    @pytest.mark.tripwire
    async def test_the_blocked_reason_reaches_the_response(self) -> None:
        """The live shape: a pending row ten hours overdue whose parent finished.
        If `blocked` does not reach the wire, the page can only show a stall."""
        res = await _server(_Db([_row()]))._handle_tasks(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 200
        body = _body(res)
        assert body["tasks"][0]["blocked"] == "terminal_parent"
        assert body["tasks"][0]["status"] == "pending"
        assert body["unseen_other_owner"] == 3

    @pytest.mark.tripwire
    async def test_an_unwired_db_CONFESSES_rather_than_returning_empty(self) -> None:
        res = await _server(None)._handle_tasks(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 503
        assert _body(res) == {"tasks": [], "wired": False}

    @pytest.mark.tripwire
    def test_it_reads_through_the_shared_reader(self) -> None:
        """Presentation over one reader, or two answers to one question."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._handle_tasks)
        assert "read_task_activity(" in src
        assert "SELECT" not in src.upper(), "the route grew its own query"

    @pytest.mark.tripwire
    def test_the_route_is_registered(self) -> None:
        import ast
        import inspect
        import textwrap

        from stackowl.control_plane import server as mod

        src = textwrap.dedent(inspect.getsource(mod.ControlPlaneServer.run))
        paths = [
            n.args[0].value
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_get"
            and isinstance(n.args[0], ast.Constant)
        ]
        assert "/api/v1/tasks" in paths, f"the route is not registered: {paths}"
