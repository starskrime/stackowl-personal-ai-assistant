"""A skill nobody owns is presented to nobody — the self-heal loop's last step.

BAKIR SENT THE PLATFORM'S OWN VERDICT, twice in two turns, and the second one is
this item. Its finding: "links were cited in the reply without being retrieved in
this run... the unverified state was only acknowledged after the fact." Fabricated
citations, delivered, disclosed afterwards.

THE PLATFORM HAD ALREADY LEARNED THIS. MEASURED 2026-09-01: it diagnosed the SAME
defect on **2026-08-30, 2026-08-31 and 2026-09-01** — three staged RCAs at roughly
140,000 tokens each — and the miner authored correct guidance every time. The
skill exists, with exactly the right trigger text::

    incident_browser_click
    when_to_use: "Before citing any source or URL, confirm it was actually
                  retrieved in this run; a stopped or blocked ..."

THE CHAIN BROKE AT THE LAST LINK. ``skill_ownership`` holds five rows and **not
one of them names a skill this miner authored**. Eleven mined skills, all
orphaned::

    incident_shell_stop            incident_web_fetch
    incident_delegate_task         incident_browser_click
    incident_owl_build_stop        incident_shell
    incident_web_fetch_stop        incident_owl_build
    incident_shell_unachieved_effect
    incident_browser_navigate_stop incident_delegate_task_unachieved_effect

``SkillInstructionInjector`` renders an OWL'S OWNED skills. An unowned skill is
written, indexed, searchable — and invisible to every owl that could use it. So
the platform diagnosed correctly, wrote the lesson correctly, and then could not
reach it, which is why it paid to learn the same thing three days running.

THE RECORDED SHAPE, EXACTLY: "Built but not wired. The capability exists, works,
and nothing calls it" — and its sibling, "a write with no reader".
``attach_skill_to_owl`` and ``persist_skill_ownership`` both existed the whole
time; the miner simply never called them.

WHICH OWLS: the ones that actually failed. ``cluster.outcomes`` carries
``owl_name`` per row, so the lesson lands where the mistake was made rather than
on every owl in the fleet.

BOTH HALVES OR NEITHER: the live overlay so THIS process presents it, and the
durable row so the next boot's hydrator restores it. Writing one without the
other is how this class of bug is made in the first place.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.learning.failure_outcome_miner import FailureCluster, FailureOutcomeMiner

pytestmark = pytest.mark.asyncio


class _Registry:
    """Records live attach calls."""

    def __init__(self) -> None:
        self.attached: list[tuple[str, str]] = []


class _Db:
    """Records durable ownership writes."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def execute(self, sql: str, params: tuple) -> None:
        self.rows.append(params)


def _cluster(*owls: str) -> FailureCluster:
    return FailureCluster(
        capability_class="browser_click",
        failure_class="stop",
        outcomes=tuple(
            SimpleNamespace(owl_name=o, trace_id=f"t-{i}", tool_sequence=())
            for i, o in enumerate(owls)
        ),
    )


def _miner(registry: object | None, db: object | None) -> FailureOutcomeMiner:
    return FailureOutcomeMiner(
        outcome_store=SimpleNamespace(),
        skill_store=SimpleNamespace(),
        skills_root=SimpleNamespace(),
        owl_registry=registry,
        db=db,
    )


async def test_the_owl_that_failed_gets_the_lesson(monkeypatch) -> None:  # noqa: ANN001
    """The whole item: before this, eleven mined skills reached nobody."""
    registry, db = _Registry(), _Db()
    import stackowl.owls.skill_ownership as own

    monkeypatch.setattr(
        own, "attach_skill_to_owl",
        lambda r, owl, skill: r.attached.append((owl, skill)) or True,
    )
    await _miner(registry, db)._attach_to_the_owls_that_failed(
        "incident_browser_click", _cluster("scout"),
    )
    assert registry.attached == [("scout", "incident_browser_click")]
    assert len(db.rows) == 1, (
        "the live overlay was written without the durable row — the ownership "
        "vanishes on the next restart, which is this bug with extra steps"
    )


async def test_every_owl_in_the_cluster_gets_it_once(monkeypatch) -> None:  # noqa: ANN001
    """De-duplicated: a cluster with six rows from two owls is two attaches."""
    registry, db = _Registry(), _Db()
    import stackowl.owls.skill_ownership as own

    monkeypatch.setattr(
        own, "attach_skill_to_owl",
        lambda r, owl, skill: r.attached.append((owl, skill)) or True,
    )
    await _miner(registry, db)._attach_to_the_owls_that_failed(
        "s", _cluster("scout", "secretary", "scout", "secretary", "scout"),
    )
    assert sorted(o for o, _ in registry.attached) == ["scout", "secretary"]


async def test_an_owl_that_never_hit_this_failure_is_not_given_the_skill() -> None:
    """Evidence, not a broadcast. Attaching to the whole fleet would bloat every
    owl's catalogue with lessons it has no use for — and the catalogue is the
    tier that gets truncated first."""
    registry, db = _Registry(), _Db()
    await _miner(registry, db)._attach_to_the_owls_that_failed("s", _cluster())
    assert registry.attached == [] and db.rows == []


async def test_an_unwired_miner_says_so_loudly(caplog) -> None:  # noqa: ANN001
    """A miner with no registry must not fail silently — silent is exactly how
    eleven orphaned skills went unnoticed. The warning is the tripwire."""
    import logging

    with caplog.at_level(logging.WARNING):
        await _miner(None, None)._attach_to_the_owls_that_failed("s", _cluster("scout"))
    assert any("no owl owns it" in r.getMessage() for r in caplog.records)


async def test_one_failing_owl_does_not_stop_the_others(monkeypatch) -> None:  # noqa: ANN001
    """A per-owl error must not cost the rest of the cluster its lesson."""
    registry, db = _Registry(), _Db()
    import stackowl.owls.skill_ownership as own

    def _attach(r: object, owl: str, skill: str) -> bool:
        if owl == "scout":
            raise RuntimeError("registry is unhappy")
        r.attached.append((owl, skill))
        return True

    monkeypatch.setattr(own, "attach_skill_to_owl", _attach)
    await _miner(registry, db)._attach_to_the_owls_that_failed(
        "s", _cluster("scout", "secretary"),
    )
    assert registry.attached == [("secretary", "s")]


async def test_bookkeeping_never_kills_a_mining_pass(monkeypatch) -> None:  # noqa: ANN001
    class _AngryDb:
        async def execute(self, *a: object, **k: object) -> None:
            raise RuntimeError("disk full")

    import stackowl.owls.skill_ownership as own

    monkeypatch.setattr(own, "attach_skill_to_owl", lambda *a: True)
    await _miner(_Registry(), _AngryDb())._attach_to_the_owls_that_failed(
        "s", _cluster("scout"),
    )  # must not raise


def test_the_attach_runs_on_the_authoring_path() -> None:
    """Structural. Authoring a skill and not attaching it is the entire defect,
    so the call must live where the write succeeds — not somewhere a later
    refactor can quietly detach it."""
    import inspect

    from stackowl.learning import failure_outcome_miner

    source = inspect.getsource(failure_outcome_miner.FailureOutcomeMiner._author_one)
    assert "_attach_to_the_owls_that_failed(" in source, (
        "the miner authors skills again without giving them to anyone — eleven "
        "orphaned skills and three RCAs of the same defect is what that costs"
    )


def test_the_miner_is_wired_with_both_collaborators() -> None:
    """A feature ships ON. With no registry and no pool the attach degrades to a
    warning, which would be the same bug wearing a log line."""
    import inspect

    from stackowl.scheduler import assembly

    source = inspect.getsource(assembly)
    build = source.split("incident_miner = FailureOutcomeMiner(")[1][:900]
    assert "owl_registry=owl_registry" in build and "db=db" in build, (
        "the incident miner is constructed without the collaborators the attach "
        "needs — it would author skills and own none of them"
    )
