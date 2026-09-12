"""What may this agent do — a question with no reader until now. A05.3.

The gap said owls "can only be created and edited by talking to the platform, and
their bounds and skills cannot be seen at all". HALF OF THAT WAS ALREADY FALSE:
A05.8 shipped `GET /api/v1/skills` the day before, rendering owl -> skill
ownership. Left as written this item would have rebuilt a surface one day old —
the ninth wrong gap in this series, failing in the same direction as the other
eight.

WHAT SURVIVED IS SHARPER. `manifest.bounds` is read by NOTHING outside authz and
the YAML persister, so an agent's AUTHORITY is visible from nowhere. And
MEASURED 2026-09-12, seven of eleven owls have neither `bounds` nor
`creation_ceiling` — which is not "default". `effective_bounds`' own docstring
says "with no defined term the result is None (genuinely unbounded)", and
`check_effective_bounds` then reads "None effective bounds (no constraint
anywhere) -> unrestricted". The live evidence agrees: secretary, verifier,
rca_gatherer and hypothesis are each presented the WHOLE registry (79 tools)
while bounded owls see 6, 8 and 11, and of 98 bounds refusals in the retained
logs ZERO are on an unbounded owl.

SO `unbounded` IS COMPUTED BY THE ENFORCEMENT PATH'S OWN FUNCTION, never
inferred from "is bounds None". A surface that reasoned its way to this answer
could disagree with the gate that enforces it, and on an authority surface that
is the whole failure.

AND THE TWO SKILL COUNTS STAY TWO. `OwlAgentManifest.skills` says it "records
ownership", which is the claim `skill_ownership` makes, and they disagree:
secretary 71 on the card against 9 rows, and only 8 of the 71 name a skill that
exists. `delivery_gate.py` already reconciles them "first owner wins", so the
disagreement is old enough to have a workaround. One merged number would be
wrong for nine of eleven owls and would hide the defect.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.control_plane.server import ControlPlaneServer
from stackowl.owls.manifest import OwlAgentManifest

_TOKEN = "t0ken-for-tests-only"


class _Req:
    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


def _owl(name: str, **over: Any) -> OwlAgentManifest:
    base: dict[str, Any] = {
        "name": name, "role": "r", "system_prompt": "p", "model_tier": "fast",
    }
    base.update(over)
    return OwlAgentManifest(**base)


def _server(manifests: list[OwlAgentManifest] | None, owned: dict[str, list[str]]) -> Any:
    """A server whose three readers are stubbed at the seam, not mocked deeper.

    The route composes `OwlStore.list_all`, `read_all_skill_ownership` and
    `read_owl_activity`; each is patched by the caller so this file tests the
    PRESENTATION and the authority computation, while the readers keep their own
    tests.
    """
    class _Cfg:
        bind_address = "127.0.0.1"
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    srv = ControlPlaneServer(_Settings(), db=(object() if manifests is not None else None))
    srv._token = _TOKEN  # noqa: SLF001
    return srv


def _body(res: Any) -> dict[str, Any]:
    return json.loads(res.text)


async def _call(
    monkeypatch: pytest.MonkeyPatch,
    manifests: list[OwlAgentManifest],
    owned: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    from stackowl.control_plane import server as mod

    class _Store:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...
        async def list_all(self) -> list[OwlAgentManifest]:
            return manifests

    async def _owned(_db: Any) -> dict[str, list[str]]:
        return owned or {}

    async def _activity(_db: Any, ms: list[Any], **_k: Any) -> tuple[list[Any], Any]:
        return [], None

    monkeypatch.setattr(mod, "OwlStore", _Store)
    monkeypatch.setattr(mod, "read_all_skill_ownership", _owned)
    monkeypatch.setattr(mod, "read_owl_activity", _activity)

    res = await _server(manifests, owned or {})._handle_agents(  # noqa: SLF001
        _Req(Authorization=f"Bearer {_TOKEN}")
    )
    assert res.status == 200, res.text
    return _body(res)


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server([], {})._handle_agents(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server([], {})._handle_agents(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}", Origin="http://evil.example",
                 Host="127.0.0.1:8787")
        )
        assert res.status == 401


class TestAuthorityIsComputedNotInferred:
    @pytest.mark.tripwire
    async def test_an_owl_with_no_bounds_and_no_ceiling_reads_UNBOUNDED(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seven of eleven live owls are exactly this shape, and the surface
        must say so rather than showing an empty bounds list that reads as
        'restricted to nothing'."""
        body = await _call(monkeypatch, [_owl("secretary")])

        row = body["agents"][0]
        assert row["unbounded"] is True
        assert row["bounds"] is None
        assert row["creation_ceiling"] is None

    @pytest.mark.tripwire
    async def test_an_owl_WITH_bounds_is_not_unbounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The vacuity control. Without it `unbounded: True` could be a constant
        and the test above would still pass."""
        body = await _call(
            monkeypatch,
            [_owl("english_tutor", bounds=BoundsSpec(tools=frozenset({"read_file"})))],
        )

        row = body["agents"][0]
        assert row["unbounded"] is False
        # The dumped shape is `BoundsSpec`'s business, not this surface's — it
        # carries every axis that has a value, and `caps` defaults to `{}`
        # rather than None. Asserting the whole dict here would make this test
        # fail the day an axis gains a default, which is the model's change to
        # make. What this route owes the reader is the tools axis, verbatim.
        assert row["bounds"]["tools"] == ["read_file"]

    @pytest.mark.tripwire
    async def test_a_CEILING_alone_is_enough_to_bound_an_owl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`effective_bounds` folds BOTH terms, so reading only `bounds` would
        call this owl unbounded while the gate restricts it. This is the exact
        disagreement the computed field exists to prevent."""
        body = await _call(
            monkeypatch,
            [_owl("scout", creation_ceiling=BoundsSpec(tools=frozenset({"memory"})))],
        )

        assert body["agents"][0]["unbounded"] is False

    @pytest.mark.tripwire
    def test_the_verdict_comes_from_the_ENFORCEMENT_function(self) -> None:
        """One source. A surface that re-derived this could drift from the gate,
        and on authority a drifting surface is worse than none."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._handle_agents)
        assert "effective_bounds(" in src, (
            "the route stopped asking the enforcement path and now decides "
            "authority for itself"
        )


class TestTheTwoSkillAnswersStayTwo:
    @pytest.mark.tripwire
    async def test_card_and_table_are_reported_separately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MEASURED: secretary declares 71 and owns 9. One merged number would be
        wrong for nine of eleven owls and would hide that the stores disagree."""
        body = await _call(
            monkeypatch,
            [_owl("secretary", skills=("a", "b", "c"))],
            {"secretary": ["a"]},
        )

        row = body["agents"][0]
        assert row["skills_on_card"] == 3
        assert row["skills_owned"] == 1
        assert "skills" not in row, (
            "a single merged skills field appeared — it picks a side in a "
            "disagreement the platform itself works around"
        )


class TestItConfessesWhenItCannotLook:
    @pytest.mark.tripwire
    async def test_an_unwired_db_CONFESSES_rather_than_returning_empty(self) -> None:
        res = await _server(None, {})._handle_agents(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}")
        )

        assert res.status == 503
        assert _body(res) == {"agents": [], "wired": False}

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
        assert "/api/v1/agents" in paths, f"the route is not registered: {paths}"
