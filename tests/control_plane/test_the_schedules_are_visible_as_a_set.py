"""Eight cron verbs already shipped, and still nobody could see the set.

A05.4's gap first read "cron jobs are invisible unless the operator queries the
database by hand". MEASURED 2026-09-11 and corrected before any of this was
built: `tools/scheduling/cronjob.py` declares EIGHT actions — create, watch,
list, update, pause, resume, remove, run — every verb the item asked for. An
item built on that gap would have rebuilt a working tool.

WHAT IS ACTUALLY MISSING is narrower and real: every one of those verbs needs
somebody AT A CHAT PROMPT. There is no view of the set from anywhere else, and
the operator's requirement was a control plane.

So this route READS THROUGH THE LIVE SCHEDULER — `list_jobs()`, the same method
the cron tool's own `list` calls — rather than issuing SQL. A second reader of
one table is how two answers to one question are born, and this tree has paid
for that shape repeatedly.

AND THE ITEM'S REAL WORK WAS THE GUARD, NOT THE ROUTE. Until now there was one
route, so the origin check, the token check and the READ check sat inline in
`_handle_health` — honest for one, two copies of one rule for two. The guard
test was worse than the duplication: it read `inspect.getsource(_handle_health)`
and asserted the ordering inside that ONE NAMED HANDLER, so a second route with
no origin check would have passed it untouched. The decision is extracted, and
`test_every_registered_route_goes_through_the_guard` derives the route list from
`run()` itself so the rule covers routes nobody has written yet.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.control_plane.auth import ALL_SEVERITIES, READ, ControlPrincipal
from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t0ken-for-tests-only"


class _Req:
    """The two things a handler asks a request for."""

    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


class _Job:
    """The shape `JobScheduler.list_jobs()` returns, field for field.

    Named attributes rather than a Mock: a Mock answers every attribute, so a
    handler that reads a field the real `Job` does not have would pass. Every
    name here was read off `Job.model_fields` on 2026-09-11.
    """

    def __init__(self, job_id: str, *, failure_count: int = 0, last_error: str | None = None) -> None:
        self.job_id = job_id
        self.handler_name = "morning_brief"
        self.schedule = "0 7 * * *"
        self.idempotency_key = f"{job_id}-key"
        self.last_run_at = "2026-09-11T07:00:00+00:00"
        self.next_run_at = "2026-09-12T07:00:00+00:00"
        self.status = "pending"
        self.retry_count = 0
        self.failure_count = failure_count
        self.last_error = last_error
        self.enabled = True


class _Scheduler:
    def __init__(self, jobs: list[_Job]) -> None:
        self._jobs = jobs
        self.calls = 0

    async def list_jobs(self) -> list[_Job]:
        self.calls += 1
        return self._jobs


def _server(*, scheduler: Any = None) -> ControlPlaneServer:
    class _Cfg:
        bind_address = "127.0.0.1"
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    srv = ControlPlaneServer(_Settings(), scheduler=scheduler)  # type: ignore[arg-type]
    srv._token = _TOKEN  # noqa: SLF001 — the running server holds it; no mint here
    return srv


def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.text)


class TestTheRouteExists:
    @pytest.mark.tripwire
    def test_it_is_registered_and_not_merely_written(self) -> None:
        """Built-but-not-wired is this tree's commonest defect, and a handler
        that no route table names is exactly that."""
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
        assert "/api/v1/schedules" in paths, f"the route is not registered: {paths}"


class TestItIsLockedLikeEveryOtherRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        srv = _server(scheduler=_Scheduler([]))
        res = await srv._handle_schedules(_Req())  # noqa: SLF001
        assert res.status == 401

    @pytest.mark.tripwire
    async def test_a_wrong_credential_is_refused(self) -> None:
        srv = _server(scheduler=_Scheduler([]))
        res = await srv._handle_schedules(_Req(Authorization="Bearer wrong"))  # noqa: SLF001
        assert res.status == 401

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        """ORDER IS BEHAVIOUR, proven by behaviour rather than by reading source.

        A browser request from another origin may carry a valid credential. The
        companion test in the locked-before-built suite asserts the ordering
        inside `_guard`; this one asserts the consequence, so the property
        survives a refactor that moves the calls around.
        """
        srv = _server(scheduler=_Scheduler([_Job("j1")]))
        res = await srv._handle_schedules(  # noqa: SLF001
            _Req(
                Authorization=f"Bearer {_TOKEN}",
                Origin="http://evil.example",
                Host="127.0.0.1:8787",
            )
        )
        assert res.status == 401

    @pytest.mark.tripwire
    async def test_a_principal_that_may_not_read_gets_403_not_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403 and 401 are different answers: one says "who are you", the other
        says "not you". Collapsing them would make `granted` decorative."""
        from stackowl.control_plane import server as mod

        monkeypatch.setattr(
            mod,
            "authenticate",
            lambda *_a, **_k: ControlPrincipal(
                principal_id="p", credential_id="c", granted=frozenset()
            ),
        )
        srv = _server(scheduler=_Scheduler([_Job("j1")]))
        res = await srv._handle_schedules(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001
        assert res.status == 403

    @pytest.mark.tripwire
    def test_read_is_the_severity_a_list_needs(self) -> None:
        """A control against the opposite error — gating a read behind WRITE
        would lock out exactly the credential this route is for."""
        principal = ControlPrincipal(
            principal_id="p", credential_id="c", granted=ALL_SEVERITIES
        )
        assert principal.may(READ)


class TestItAnswersWithTheWholeSet:
    @pytest.mark.tripwire
    async def test_every_job_is_listed_with_what_an_operator_needs(self) -> None:
        jobs = [
            _Job("morning-brief"),
            _Job("retry-sweep", failure_count=3, last_error="db is locked"),
        ]
        sched = _Scheduler(jobs)
        srv = _server(scheduler=sched)

        res = await srv._handle_schedules(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert res.status == 200
        body = _body(res)
        assert body["wired"] is True
        assert [s["job_id"] for s in body["schedules"]] == ["morning-brief", "retry-sweep"]

        failing = body["schedules"][1]
        assert failing["failure_count"] == 3, (
            "the list shows a job exists but not that it has been failing — "
            "which is the A05.5 shape, a surface that answers and leaves the "
            "reader worse off than silence"
        )
        assert failing["last_error"] == "db is locked"
        assert failing["enabled"] is True
        assert failing["next_run_at"]

    @pytest.mark.tripwire
    async def test_it_asks_the_LIVE_scheduler_rather_than_the_database(self) -> None:
        """One reader, or two answers to one question.

        `list_jobs()` is what `cronjob.py`'s own `list` calls. A route with its
        own SELECT would drift from it silently — and drift between a chat turn
        and a dashboard is indistinguishable from a bug in either.
        """
        sched = _Scheduler([_Job("j1")])
        srv = _server(scheduler=sched)

        await srv._handle_schedules(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert sched.calls == 1, "the handler did not go through `list_jobs()`"

    @pytest.mark.tripwire
    def test_the_server_module_issues_no_sql_of_its_own(self) -> None:
        """The structural half of the rule above. A behaviour test proves this
        handler asks the scheduler; this proves no route can quietly stop."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod).upper()
        for sql in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "):
            assert sql not in src, (
                f"the control plane issues its own {sql.strip()} — it must read "
                "through the component that owns the table"
            )

    @pytest.mark.tripwire
    async def test_an_unwired_scheduler_CONFESSES_rather_than_returning_empty(self) -> None:
        """An empty list and a missing collaborator are different facts, and a
        read failure that looks like an empty store is a named item in this
        programme (A06.6). 503 + `wired: False` cannot be mistaken for "you have
        no schedules"."""
        srv = _server(scheduler=None)

        res = await srv._handle_schedules(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert res.status == 503
        assert _body(res) == {"schedules": [], "wired": False}

    @pytest.mark.tripwire
    async def test_no_schedules_is_an_empty_set_and_still_a_200(self) -> None:
        """The other side of the same distinction: genuinely having none is a
        successful answer, not an error."""
        srv = _server(scheduler=_Scheduler([]))

        res = await srv._handle_schedules(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert res.status == 200
        assert _body(res) == {"wired": True, "schedules": []}
