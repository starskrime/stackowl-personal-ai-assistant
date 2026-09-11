"""Which owl owns which skill — visible to somebody who is not at a chat prompt.

A05.8's gap NAMED ITS OWN READER, and that is the whole design of this route:
`owls/skill_ownership.py::read_all_skill_ownership` already returns
`owl_name -> [skill names]`, owner-scoped, and three internal consumers call it
(`learning/failure_outcome_miner.py`, `pipeline/delivery_gate.py`,
`scheduler/handlers/incident_escalation.py`). None of them is a surface. So the
state is stored, read, and reaches no operator — DEBT-307's shape, the one that
accounted for seven of nine gaps in this series: a capability that EXISTS and is
UNREACHABLE.

MEASURED 2026-09-11 before building: 35 ownership rows across SEVEN owls
(verifier 12, secretary 9, scout 4, rca_gatherer 4, jobmarket 3, mailbutler 2,
hypothesis 1) against 46 skills, and `commands/skill_command.py` mentions
ownership NOWHERE — its three `owner` matches are GitHub URL parsing
(`owner/repo`).

AND THE ITEM'S OWN `gap_check` WAS REPAIRED IN THE SAME CHANGE, because it could
never have closed. It grepped `commands/skill_command.py` for the word
"ownership", so shipping a CONTROL-PLANE route left it reading HOLDS forever —
proven by running it against the patched tree before landing. The gap says "no
command **or surface**" and the check watched one file. Swept rather than
assumed: of ten `gap_check`s in this file, two pin a literal path and the other
(A05.2) pins the file its own gap names, so this is one instance, not a class —
no guard built, no sweep queued.
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


class _FakeDb:
    """Asserts the READER keeps its owner predicate.

    A single-principal install makes a scoped and an unscoped SELECT
    indistinguishable in their results, which is how an unscoped one survives
    review (DEBT-292). The assertion lives inside the fake, so a tenancy
    regression fails HERE rather than at a tripwire that may not be derived.
    """

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows
        self.seen: list[str] = []

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, str]]:
        assert "owner_id" in sql, (
            "the skill-ownership reader lost its owner predicate — every row in "
            "this table belongs to a principal"
        )
        self.seen.append(sql)
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


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server()._handle_skills(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server()._handle_skills(  # noqa: SLF001
            _Req(
                Authorization=f"Bearer {_TOKEN}",
                Origin="http://evil.example",
                Host="127.0.0.1:8787",
            )
        )
        assert res.status == 401


class TestItAnswersWithTheWholeSet:
    @pytest.mark.tripwire
    async def test_every_owl_is_listed_with_its_skills_sorted(self) -> None:
        db = _FakeDb(
            [
                {"owl_name": "verifier", "skill_name": "b"},
                {"owl_name": "scout", "skill_name": "c"},
                {"owl_name": "verifier", "skill_name": "a"},
            ]
        )
        res = await _server(db)._handle_skills(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 200
        assert _body(res) == {
            "wired": True,
            "owls": [
                {"owl": "scout", "skills": ["c"], "count": 1},
                {"owl": "verifier", "skills": ["a", "b"], "count": 2},
            ],
        }
        assert len(db.seen) == 1, (
            "the route issued more than one query — a second SELECT over "
            f"skill_ownership is a second answer to one question: {db.seen}"
        )

    @pytest.mark.tripwire
    async def test_an_unwired_db_CONFESSES_rather_than_returning_empty(self) -> None:
        """A05.5's shape. An owl that owns nothing and a platform that cannot
        look are different answers, and `{"owls": []}` with a 200 conflates
        them — which is exactly how `committed_facts` read as an archive with no
        writer while 361 memories sat one table over."""
        res = await _server(None)._handle_skills(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 503
        assert _body(res) == {"owls": [], "wired": False}


class TestItIsPresentationOverOneReader:
    @pytest.mark.tripwire
    def test_it_reads_through_the_reader_the_gap_itself_names(self) -> None:
        """One reader, or two answers to one question. A second SELECT over
        `skill_ownership` is the two-copies-of-one-rule shape, and it is how a
        surface drifts from what the platform actually acts on."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._handle_skills)
        assert "read_all_skill_ownership(" in src, (
            "the route no longer asks the reader the gap named"
        )

    @pytest.mark.tripwire
    def test_the_route_is_registered(self) -> None:
        """Built-but-not-wired, on the item whose whole point is reachability.

        The OTHER half — that the page actually fetches it — is deliberately NOT
        asserted here. `test_every_api_route_is_rendered_somewhere_on_the_page`
        already derives it from the route table, and I nearly shipped a third
        copy of it into this file because I read the first matching test in that
        module and stopped. What the same reading DID find is a stale,
        hand-written half of the same rule sitting one method above it, deleted
        in this change.
        """
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
        assert "/api/v1/skills" in paths, f"the route is not registered: {paths}"
