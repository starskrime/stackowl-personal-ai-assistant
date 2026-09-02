"""Ownership had no reconciler, so a writer that forgot it orphaned a lesson for ever.

WHAT THIS IS THE FIX FOR. Eleven skills the incident miner authored had no
``skill_ownership`` row and never had had one. ``SkillInstructionInjector``
renders an OWL'S OWNED skills, so all eleven were written, indexed, searchable —
and presented to nobody. The same defect was re-diagnosed on three consecutive
days at roughly 140,000 tokens a time, for guidance the platform had already
written down and could not reach.

Attaching at authoring time shipped on 2026-09-01 and fixes only the NEXT skill.
Asked which of the eleven to attach by hand, Bakir refused the question:

    "Need to fix why it is not attached and what self-healing is not healing it."

He is right, and the measurement agrees. TWO causes, both live:

1. OWNERSHIP HAD NO RECONCILER. It was a side effect every writer had to
   remember, so any writer that forgot produced an orphan in silence. Nothing on
   the platform ever asked "does this learned skill belong to anyone?"

2. THE PROVENANCE THAT WOULD ANSWER "WHOSE?" WAS BEING DESTROYED. ``parent_traces``
   is written at authoring time from live outcomes, no SKILL.md frontmatter
   carries it (measured: zero of the learned skills on disk have the key), and the
   loader's upsert set ``parent_traces = excluded.parent_traces`` — so the next
   boot overwrote it with ``[]``. All eleven read ``[]`` today. Even a perfect
   reconciler could not have said which owls a lesson came from.

So the reconciler reads the owls from the CLUSTER, recomputed from live outcomes
on every pass — the same source the authoring-time attach uses — and the upsert
no longer erases provenance going forward.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from stackowl.learning.failure_outcome_miner import FailureCluster, FailureOutcomeMiner

pytestmark = pytest.mark.asyncio


class _Registry:
    def __init__(self) -> None:
        self.attached: list[tuple[str, str]] = []


class _Db:
    """Serves ownership reads and records ownership writes."""

    def __init__(self, owned: dict[str, list[str]] | None = None) -> None:
        self.owned = owned or {}
        self.rows: list[tuple] = []

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        return [
            {"owl_name": owl, "skill_name": skill}
            for owl, skills in self.owned.items()
            for skill in skills
        ]

    async def execute(self, sql: str, params: tuple) -> None:
        self.rows.append(params)


class _Skills:
    def __init__(self, *names: str) -> None:
        self._names = names

    async def list_for_source(self, source: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=n) for n in self._names]


def _cluster(capability: str, failure: str, *owls: str) -> FailureCluster:
    return FailureCluster(
        capability_class=capability,
        failure_class=failure,
        outcomes=tuple(
            SimpleNamespace(owl_name=o, trace_id=f"t-{i}", tool_sequence=())
            for i, o in enumerate(owls)
        ),
    )


def _miner(registry: object | None, db: object | None, skills: object) -> FailureOutcomeMiner:
    return FailureOutcomeMiner(
        outcome_store=SimpleNamespace(),
        skill_store=skills,
        skills_root=SimpleNamespace(),
        owl_registry=registry,
        db=db,
    )


@pytest.fixture
def _attach(monkeypatch):  # noqa: ANN001, ANN201
    import stackowl.owls.skill_ownership as own

    monkeypatch.setattr(
        own, "attach_skill_to_owl",
        lambda r, owl, skill: r.attached.append((owl, skill)) or True,
    )


async def test_an_orphaned_skill_is_GIVEN_to_the_owls_that_failed(_attach) -> None:  # noqa: ANN001
    """The eleven, healed by the mechanism instead of by hand."""
    registry, db = _Registry(), _Db()
    miner = _miner(registry, db, _Skills("incident_browser_click"))

    healed = await miner.reconcile_ownership(
        [_cluster("browser_click", "stop", "scout", "secretary")],
    )

    assert healed == 1
    assert sorted(registry.attached) == [
        ("scout", "incident_browser_click"),
        ("secretary", "incident_browser_click"),
    ]
    assert len(db.rows) == 2, (
        "the live overlay was written without the durable row — the ownership "
        "vanishes on the next restart, which is this bug with extra steps"
    )


async def test_the_PAIR_FORM_orphans_are_reachable_too(_attach) -> None:  # noqa: ANN001
    """Most of the eleven are pre-rename names. A reconciler that only knew the
    merged spelling would leave them exactly as orphaned as it found them."""
    registry, db = _Registry(), _Db()
    miner = _miner(registry, db, _Skills("incident_shell_stop"))

    assert await miner.reconcile_ownership([_cluster("shell", "stop", "secretary")]) == 1
    assert registry.attached == [("secretary", "incident_shell_stop")]


async def test_a_skill_that_ALREADY_has_an_owner_is_left_alone(_attach) -> None:  # noqa: ANN001
    """Idempotence, and it must hold at a pass every ~12 minutes for ever."""
    registry = _Registry()
    db = _Db(owned={"scout": ["incident_browser_click"]})
    miner = _miner(registry, db, _Skills("incident_browser_click"))

    assert await miner.reconcile_ownership([_cluster("browser_click", "stop", "scout")]) == 0
    assert registry.attached == []
    assert db.rows == []


async def test_a_cluster_whose_skill_was_never_written_attaches_NOTHING(_attach) -> None:  # noqa: ANN001
    """The control. Without it the tests above pass for a reconciler that
    attaches every name it can spell, owned skill or not."""
    registry, db = _Registry(), _Db()
    miner = _miner(registry, db, _Skills("incident_web_fetch"))

    assert await miner.reconcile_ownership([_cluster("shell", "stop", "secretary")]) == 0
    assert registry.attached == []


async def test_a_cluster_with_no_owl_names_attaches_NOTHING(_attach) -> None:  # noqa: ANN001
    """Evidence, not a guess: an outcome with no owl cannot say who failed."""
    registry, db = _Registry(), _Db()
    miner = _miner(registry, db, _Skills("incident_shell"))

    assert await miner.reconcile_ownership([_cluster("shell", "stop")]) == 0
    assert registry.attached == []


async def test_an_unreadable_ownership_table_costs_the_pass_NOTHING(_attach) -> None:  # noqa: ANN001
    """Hygiene may never stop the miner authoring the skill an incident needs."""
    registry, db = _Registry(), _Db()

    async def _boom(*a: object, **k: object) -> list:
        raise RuntimeError("ownership read failed")

    db.fetch_all = _boom  # type: ignore[method-assign]
    miner = _miner(registry, db, _Skills("incident_shell"))

    assert await miner.reconcile_ownership([_cluster("shell", "stop", "scout")]) == 0


async def test_without_a_registry_or_db_it_does_nothing_rather_than_half_of_it() -> None:
    """Writing the overlay with no pool, or the row with no registry, is how a
    skill ends up owned in one process and orphaned in the next."""
    miner = _miner(None, _Db(), _Skills("incident_shell"))
    assert await miner.reconcile_ownership([_cluster("shell", "stop", "scout")]) == 0
    miner = _miner(_Registry(), None, _Skills("incident_shell"))
    assert await miner.reconcile_ownership([_cluster("shell", "stop", "scout")]) == 0
