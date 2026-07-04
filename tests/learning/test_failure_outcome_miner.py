"""Tests for Task 5 — FailureOutcomeMiner (incident-clustering miner).

Sibling to test_tool_heuristic.py's ToolOutcomeMiner tests. This miner does
the INVERSE of the positive-only success miner: it clusters FAILED
TaskOutcome rows and, given a verified RcaVerdict for a cluster (Task 6/7's
future output — hand-built here), authors a learned SKILL.md through the
SAME gated_skill_write chokepoint Task 4 introduced for the success path.

Post-review fixes (round 2):
* Data source: failures are read via TaskOutcomeStore.list_failed_global,
  which does NOT require quality_score IS NOT NULL — the critic never scores
  a failure (positive-only learning), so a query gated on quality_score would
  never see one. _seed_failures below deliberately does NOT call
  set_quality_score, matching what the real pipeline actually produces.
* Clustering grain: (capability_class, failure_class), not (tool_name,
  failure_class) — capability_class resolves via an injected
  capability_tag_lookup so sibling tools sharing a capability_tag (per
  stackowl.pipeline.capability_substitution) combine into one cluster.
"""

from __future__ import annotations

import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.failure_outcome_miner import (
    FailureCluster,
    FailureOutcomeMiner,
    RcaVerdict,
    cluster_failures_by_capability_and_signature,
)
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


def _outcome(
    trace_id: str, tool: str, failure_class: str | None, *,
    quality: float | None = None,
) -> TaskOutcome:
    """Build a TaskOutcome. Failures default to quality_score=None/scored_at=None
    — matching reality: the critic (positive-only) never scores a failure."""
    return TaskOutcome(
        outcome_id=0, trace_id=trace_id, session_id="s", owl_name="o",
        channel="cli", success=failure_class is None, latency_ms=100.0,
        tool_call_count=1, tool_sequence=(tool,), failure_class=failure_class,
        quality_score=quality, step_durations={}, input_text="in",
        response_text="out", captured_at=time.time(),
        scored_at=time.time() if quality is not None else None,
    )


# ---------- pure clustering -------------------------------------------------


def test_cluster_groups_by_tool_and_failure_class() -> None:
    outs = [
        _outcome("1", "web_fetch", "ToolTimeoutError"),
        _outcome("2", "web_fetch", "ToolTimeoutError"),
        _outcome("3", "web_fetch", "ToolTimeoutError"),
        _outcome("4", "shell", "PermissionError"),
    ]
    clusters = cluster_failures_by_capability_and_signature(outs, min_size=3)
    assert len(clusters) == 1
    assert clusters[0].capability_class == "web_fetch"  # no lookup -> tool_name fallback
    assert clusters[0].failure_class == "ToolTimeoutError"
    assert clusters[0].size == 3
    assert clusters[0].key == ("web_fetch", "ToolTimeoutError")


def test_cluster_separates_different_failure_classes_same_tool() -> None:
    outs = [
        _outcome("1", "web_fetch", "ToolTimeoutError"),
        _outcome("2", "web_fetch", "ToolTimeoutError"),
        _outcome("3", "web_fetch", "ToolTimeoutError"),
        _outcome("4", "web_fetch", "ConnectionRefusedError"),
        _outcome("5", "web_fetch", "ConnectionRefusedError"),
        _outcome("6", "web_fetch", "ConnectionRefusedError"),
    ]
    clusters = cluster_failures_by_capability_and_signature(outs, min_size=3)
    keys = {c.key for c in clusters}
    assert keys == {
        ("web_fetch", "ToolTimeoutError"),
        ("web_fetch", "ConnectionRefusedError"),
    }


def test_cluster_ignores_successful_outcomes() -> None:
    """Inverse of ToolOutcomeMiner's positive-only directive: this miner must
    NEVER cluster a successful (failure_class is None) outcome."""
    outs = [
        _outcome("1", "web_fetch", None, quality=0.9),
        _outcome("2", "web_fetch", None, quality=0.9),
        _outcome("3", "web_fetch", None, quality=0.9),
    ]
    assert cluster_failures_by_capability_and_signature(outs, min_size=3) == []


def test_cluster_below_threshold_is_dropped() -> None:
    outs = [
        _outcome("1", "web_fetch", "ToolTimeoutError"),
        _outcome("2", "web_fetch", "ToolTimeoutError"),
    ]
    assert cluster_failures_by_capability_and_signature(outs, min_size=3) == []


def test_cluster_ignores_empty_tool_sequence() -> None:
    empty_seq = TaskOutcome(
        outcome_id=0, trace_id="e", session_id="s", owl_name="o", channel="cli",
        success=False, latency_ms=1.0, tool_call_count=0, tool_sequence=(),
        failure_class="X", quality_score=None, step_durations={},
        input_text="i", response_text="o", captured_at=time.time(), scored_at=None,
    )
    assert cluster_failures_by_capability_and_signature([empty_seq], min_size=1) == []


def test_failure_cluster_is_plain_dataclass() -> None:
    fc = FailureCluster(
        capability_class="web_fetch", failure_class="ToolTimeoutError",
        outcomes=(_outcome("1", "web_fetch", "ToolTimeoutError"),),
    )
    assert fc.size == 1
    assert fc.key == ("web_fetch", "ToolTimeoutError")


# ---------- clustering grain: capability_tag, not raw tool_name -------------


def test_cluster_combines_sibling_tools_sharing_capability_tag() -> None:
    """web_fetch and browser_browse share capability_tag='web_knowledge' (per
    stackowl.pipeline.capability_substitution). 2 web_fetch timeouts + 2
    browser_browse timeouts must combine into ONE 4-evidence cluster —
    raw tool_name clustering would split this into two below-threshold-3
    buckets and silently drop the incident."""
    tag_lookup = {"web_fetch": "web_knowledge", "browser_browse": "web_knowledge"}.get
    outs = [
        _outcome("1", "web_fetch", "ToolTimeoutError"),
        _outcome("2", "web_fetch", "ToolTimeoutError"),
        _outcome("3", "browser_browse", "ToolTimeoutError"),
        _outcome("4", "browser_browse", "ToolTimeoutError"),
    ]
    # Without the lookup: two below-threshold buckets of 2, both dropped.
    assert cluster_failures_by_capability_and_signature(outs, min_size=3) == []
    # With the lookup: one combined cluster of 4, crosses the threshold.
    clusters = cluster_failures_by_capability_and_signature(
        outs, min_size=3, capability_tag_lookup=tag_lookup,
    )
    assert len(clusters) == 1
    assert clusters[0].capability_class == "web_knowledge"
    assert clusters[0].failure_class == "ToolTimeoutError"
    assert clusters[0].size == 4


def test_cluster_falls_back_to_tool_name_when_tag_unregistered() -> None:
    """A lookup is supplied but doesn't cover this tool -> falls back to the
    raw tool_name as its own capability class (documented, not silent)."""
    tag_lookup = {"web_fetch": "web_knowledge"}.get  # "shell" not covered
    outs = [
        _outcome("1", "shell", "PermissionError"),
        _outcome("2", "shell", "PermissionError"),
        _outcome("3", "shell", "PermissionError"),
    ]
    clusters = cluster_failures_by_capability_and_signature(
        outs, min_size=3, capability_tag_lookup=tag_lookup,
    )
    assert len(clusters) == 1
    assert clusters[0].capability_class == "shell"


# ---------- data source: list_failed_global (no quality_score required) ----


async def test_list_failed_global_returns_failures_without_quality_score(
    tmp_db: DbPool,
) -> None:
    """Regression for the Critical finding: a failed outcome NEVER gets a
    quality_score (the critic only scores successes), so the miner's data
    source must NOT require quality_score IS NOT NULL."""
    store = TaskOutcomeStore(tmp_db)
    await store.record(
        trace_id="fail-1", session_id="s", owl_name="scout", channel="cli",
        success=False, latency_ms=5000.0, tool_call_count=1,
        failure_class="ToolTimeoutError", step_durations={},
        input_text="task", response_text="(error)", tool_sequence=("web_fetch",),
    )
    out = await store.get_by_trace_id("fail-1")
    assert out is not None
    assert out.quality_score is None  # never scored — exactly like production

    # The OLD (wrong) data source would find nothing:
    scored = await store.list_scored_for_owl_global(since_epoch=0.0)
    assert scored == []

    # The NEW direct-failure query finds it anyway:
    failed = await store.list_failed_global(since_epoch=0.0)
    assert len(failed) == 1
    assert failed[0].trace_id == "fail-1"
    assert failed[0].quality_score is None


# ---------- end-to-end miner (db + gated skill write) -----------------------


def _allow_gate(scheduled_name: str) -> ConsequentialActionGate:
    return ConsequentialActionGate(
        ConsentPolicy(tiers={scheduled_name: TrustTier.AUTO})
    )


async def _seed_failures(
    db: DbPool, *, n: int = 3, tool: str = "web_fetch",
    failure_class: str = "ToolTimeoutError",
) -> None:
    """Seed n failed outcomes WITHOUT setting quality_score — real failures
    never get one (the critic only scores successes; see F-51 in
    outcome_store.py). Previously this helper called set_quality_score(),
    which masked the fact that the miner's original query would never see a
    real-world failure."""
    store = TaskOutcomeStore(db)
    for i in range(n):
        tid = f"fail-{tool}-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=False, latency_ms=5000.0, tool_call_count=1,
            failure_class=failure_class, step_durations={},
            input_text=f"task {i}", response_text="(error)",
            tool_sequence=(tool,),
        )


def _verdict(
    *, capability_class: str = "web_fetch", failure_class: str = "ToolTimeoutError",
    skill_name: str = "web-fetch-timeout-fix", verified: bool = True,
) -> RcaVerdict:
    return RcaVerdict(
        capability_class=capability_class, failure_class=failure_class,
        skill_name=skill_name,
        description="Avoid web_fetch timeouts on slow hosts",
        when_to_use="web_fetch keeps timing out against this host",
        root_cause="The host's DNS resolves to an IPv6-only record the sandbox can't route to.",
        fix_pattern="Force IPv4 resolution before retrying the fetch once.",
        verified=verified,
        parent_trace_ids=("fail-web_fetch-0",),
    )


@pytest.fixture()
async def miner_env(tmp_db: DbPool, tmp_path):
    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    yield tmp_db, skills_root, components.store


async def test_mine_authors_skill_for_verified_verdict(miner_env) -> None:
    db, root, store = miner_env
    await _seed_failures(db, n=3)  # note: quality_score never set (see helper docstring)
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=_allow_gate("failure_outcome_miner_scheduled"),
        min_evidence=3,
    )
    verdict = _verdict()
    report = await miner.mine({verdict.key: verdict})
    assert report.n_clusters_found == 1
    assert report.n_skills_written == 1
    written = root / "learned" / "web-fetch-timeout-fix" / "SKILL.md"
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "name: web-fetch-timeout-fix" in text
    assert "IPv6-only" in text
    assert "Force IPv4" in text
    sk = await store.get("learned", "web-fetch-timeout-fix")
    assert sk is not None


async def test_mine_combines_sibling_capability_via_lookup(miner_env) -> None:
    """End-to-end proof of the Important fix: 2 web_fetch + 2 browser_browse
    timeouts, sharing capability_tag='web_knowledge', combine into ONE
    4-evidence cluster (crossing min_evidence=3) and get authored — neither
    tool alone would reach the threshold."""
    db, root, store = miner_env
    await _seed_failures(db, n=2, tool="web_fetch")
    await _seed_failures(db, n=2, tool="browser_browse")
    tag_lookup = {"web_fetch": "web_knowledge", "browser_browse": "web_knowledge"}.get
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=_allow_gate("failure_outcome_miner_scheduled"),
        min_evidence=3, capability_tag_lookup=tag_lookup,
    )
    verdict = _verdict(capability_class="web_knowledge", skill_name="web-knowledge-timeout-fix")
    report = await miner.mine({verdict.key: verdict})
    assert report.n_clusters_found == 1
    assert report.n_skills_written == 1
    assert (root / "learned" / "web-knowledge-timeout-fix" / "SKILL.md").exists()


async def test_mine_skips_cluster_without_verdict(miner_env) -> None:
    db, root, store = miner_env
    await _seed_failures(db, n=3)
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=_allow_gate("failure_outcome_miner_scheduled"),
        min_evidence=3,
    )
    report = await miner.mine({})
    assert report.n_clusters_found == 1
    assert report.n_skills_written == 0
    assert not (root / "learned").exists() or list((root / "learned").iterdir()) == []


async def test_mine_skips_unverified_verdict(miner_env) -> None:
    db, root, store = miner_env
    await _seed_failures(db, n=3)
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=_allow_gate("failure_outcome_miner_scheduled"),
        min_evidence=3,
    )
    verdict = _verdict(verified=False)
    report = await miner.mine({verdict.key: verdict})
    assert report.n_skills_written == 0
    assert not (root / "learned" / verdict.skill_name).exists()


async def test_mine_skips_cluster_below_threshold(miner_env) -> None:
    db, root, store = miner_env
    await _seed_failures(db, n=2)  # below min_evidence=3
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=_allow_gate("failure_outcome_miner_scheduled"),
        min_evidence=3,
    )
    verdict = _verdict()
    report = await miner.mine({verdict.key: verdict})
    assert report.n_clusters_found == 0
    assert report.n_skills_written == 0


async def test_mine_denied_consent_writes_nothing(miner_env) -> None:
    """Same regression shape as Task 4's synthesizer test: a DENIED consent
    must leave NO file on disk and NEVER call store.upsert."""
    db, root, store = miner_env
    await _seed_failures(db, n=3)
    deny_gate = ConsequentialActionGate(ConsentPolicy(tiers={}))  # no AUTO tier -> ALWAYS_ASK off-TTY fails closed
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root, consent_gate=deny_gate, min_evidence=3,
    )
    verdict = _verdict()
    report = await miner.mine({verdict.key: verdict})
    assert report.n_skills_written == 0
    assert not (root / "learned" / verdict.skill_name).exists()
    assert await store.get("learned", verdict.skill_name) is None


async def test_mine_no_gate_wired_fails_closed(miner_env) -> None:
    db, root, store = miner_env
    await _seed_failures(db, n=3)
    miner = FailureOutcomeMiner(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        skills_root=root,  # consent_gate defaults to None
        min_evidence=3,
    )
    verdict = _verdict()
    report = await miner.mine({verdict.key: verdict})
    assert report.n_skills_written == 0
    assert not (root / "learned" / verdict.skill_name).exists()
