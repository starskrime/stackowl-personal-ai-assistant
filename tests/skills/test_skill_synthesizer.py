"""Tests for Learning Commit 3 sub-phase 3c — SkillSynthesizer + Handler."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.synthesizer import (
    _CONSENT_TOOL_NAME_SCHEDULED,
    _DEFAULT_VERIFICATION_SECTION,
    _VERIFICATION_HEADING,
    SkillSynthesizer,
    SkillSynthesizerPromptBuilder,
    ToolSequenceCluster,
    cluster_outcomes_by_tool_sequence,
    parse_new_skill_response,
    parse_refined_body,
)
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


def _allow_gate() -> ConsequentialActionGate:
    """A REAL consent gate configured to auto-allow the synthesizer's identity.

    Regression fixture for Task 4: SkillSynthesizer's writes now route through
    security_scan_gate + consent (see stackowl.skills.authoring), so every
    pre-existing "the skill got written" test needs an explicit ALLOW grant —
    without one, consent_gate defaults to None and fails closed (writes
    nothing), by design.
    """
    return ConsequentialActionGate(
        ConsentPolicy(tiers={_CONSENT_TOOL_NAME_SCHEDULED: TrustTier.AUTO})
    )


@dataclass
class _RecordingConsentGate:
    """Test double exposing the exact ``gate.policy.request(...)`` shape the
    shared authoring helper calls, recording every request for assertions."""

    allow: bool
    calls: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    @property
    def policy(self) -> _RecordingConsentGate:
        return self

    async def request(self, **kwargs: object) -> bool:
        assert self.calls is not None
        self.calls.append(kwargs)
        return self.allow


@dataclass
class _ScriptedProvider:
    """Stub ModelProvider that returns canned strings in order.

    Also records every ``model`` kwarg passed to ``complete()`` in
    ``seen_models`` — used by the Task 18 model-threading tests below to prove
    ``SkillSynthesizer`` forwards its constructor ``model=`` into BOTH internal
    call sites (``_synthesize_one`` / discover, ``_refine_one`` / refine)
    instead of hardcoding ``model=""``.
    """

    responses: list[str]
    model_name: str = "stub-fast"

    def __post_init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.seen_models: list[str] = []
        self._idx = 0

    async def complete(self, messages: list[Message], model: str = "") -> CompletionResult:
        self.calls.append(list(messages))
        self.seen_models.append(model)
        if self._idx >= len(self.responses):
            raise RuntimeError(f"scripted provider exhausted after {self._idx} calls")
        out = self.responses[self._idx]
        self._idx += 1
        return CompletionResult(
            content=out, model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )


# ---------- parser tests ---------------------------------------------------

def test_parse_new_skill_response_round_trip() -> None:
    raw = json.dumps({
        "name": "my-skill",
        "description": "do thing",
        "when_to_use": "X",
        "body": "# step 1",
    })
    parsed = parse_new_skill_response(raw)
    assert parsed is not None
    assert parsed["name"] == "my-skill"
    # Body always contains ## Verification (appended when absent).
    assert "## Verification" in parsed["body"]
    assert "# step 1" in parsed["body"]


def test_parse_new_skill_response_coerces_name() -> None:
    raw = json.dumps({
        "name": "MY Cool Skill!!", "description": "x",
        "when_to_use": "y", "body": "z",
    })
    parsed = parse_new_skill_response(raw)
    assert parsed is not None
    assert parsed["name"] == "my-cool-skill"


def test_parse_new_skill_response_rejects_missing_keys() -> None:
    raw = json.dumps({"name": "x", "body": "y"})
    assert parse_new_skill_response(raw) is None


def test_parse_new_skill_response_rejects_empty_required_field() -> None:
    raw = json.dumps({
        "name": "x", "description": "", "when_to_use": "y", "body": "z",
    })
    assert parse_new_skill_response(raw) is None


def test_parse_refined_body_basic() -> None:
    raw = json.dumps({"body": "new improved body"})
    body = parse_refined_body(raw)
    # Body is returned (not None) and the original text is preserved.
    # A default ## Verification section is appended when the input lacks one.
    assert body is not None
    assert "new improved body" in body
    assert "## Verification" in body


def test_parse_refined_body_rejects_empty() -> None:
    raw = json.dumps({"body": "   "})
    assert parse_refined_body(raw) is None


# ---------- clustering tests -----------------------------------------------

def _outcome(
    trace_id: str, sequence: tuple[str, ...], *, quality: float = 0.8,
) -> object:
    """Helper: build a TaskOutcome the clustering function consumes."""
    from stackowl.memory.outcome_store import TaskOutcome

    return TaskOutcome(
        outcome_id=0, trace_id=trace_id, session_id="s", owl_name="o",
        channel="cli", success=True, latency_ms=100.0, tool_call_count=len(sequence),
        tool_sequence=sequence, failure_class=None, quality_score=quality,
        step_durations={}, input_text="in", response_text="out",
        captured_at=time.time(), scored_at=time.time(),
    )


def test_cluster_drops_empty_sequences() -> None:
    outs = [_outcome("a", ()), _outcome("b", ()), _outcome("c", ())]
    assert cluster_outcomes_by_tool_sequence(outs) == []


def test_cluster_groups_by_exact_sequence_and_filters_size() -> None:
    outs = [
        _outcome("1", ("web_fetch", "shell")),
        _outcome("2", ("web_fetch", "shell")),
        _outcome("3", ("web_fetch", "shell")),
        _outcome("4", ("shell", "web_fetch")),  # different sequence
        _outcome("5", ("shell", "web_fetch")),
    ]
    clusters = cluster_outcomes_by_tool_sequence(outs, min_size=3)
    assert len(clusters) == 1
    assert clusters[0].sequence == ("web_fetch", "shell")
    assert clusters[0].size == 3


def test_cluster_filters_by_mean_quality() -> None:
    outs = [
        _outcome("1", ("read_file",), quality=0.5),
        _outcome("2", ("read_file",), quality=0.6),
        _outcome("3", ("read_file",), quality=0.5),
    ]
    clusters = cluster_outcomes_by_tool_sequence(
        outs, min_size=3, min_mean_quality=0.75,
    )
    assert clusters == []


def test_cluster_proposed_slug_matches_manifest_pattern() -> None:
    cl = ToolSequenceCluster(
        sequence=("web_fetch", "shell"),
        outcomes=(_outcome("1", ("web_fetch", "shell")),),
    )
    slug = cl.proposed_slug
    import re
    assert re.match(r"^[a-z][a-z0-9_-]*$", slug)


# ---------- prompt builder tests -------------------------------------------

def test_prompt_builder_new_includes_sequence_and_samples() -> None:
    cl = ToolSequenceCluster(
        sequence=("web_fetch", "shell"),
        outcomes=(
            _outcome("1", ("web_fetch", "shell")),
            _outcome("2", ("web_fetch", "shell")),
        ),
    )
    msgs = SkillSynthesizerPromptBuilder().build_for_new(cl)
    assert len(msgs) == 2
    user = msgs[1].content
    assert "web_fetch" in user and "shell" in user
    assert '"quality_score": 0.8' in user
    assert "Cluster size: 2" in user


# ---------- end-to-end SkillSynthesizer (with scripted provider) -----------

@pytest.fixture()
async def synth_env(tmp_db: DbPool, tmp_path: Path):
    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    yield tmp_db, skills_root, components.store


async def _seed_outcomes(
    db: DbPool, *, sequence: tuple[str, ...], n: int = 3, quality: float = 0.85,
) -> None:
    store = TaskOutcomeStore(db)
    for i in range(n):
        tid = f"trace-{sequence[0]}-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=True, latency_ms=50.0, tool_call_count=len(sequence),
            failure_class=None, step_durations={},
            input_text=f"do the thing {i}", response_text="done",
            tool_sequence=sequence,
        )
        out = await store.get_by_trace_id(tid)
        assert out is not None
        await store.set_quality_score(out.outcome_id, quality)


async def test_discover_writes_skill_md_and_audits(synth_env) -> None:
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "scrape-and-process",
        "description": "Fetch web content and shell-process it",
        "when_to_use": "User wants scraped page run through a script",
        "body": "# Steps\n1. Fetch the page.\n2. Shell-process the content.",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=_allow_gate(),
        lookback_days=30, min_cluster_size=3, min_mean_quality=0.75,
    )
    n = await synth.discover_new_skills()
    assert n == 1
    written = root / "learned" / "scrape-and-process" / "SKILL.md"
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "name: scrape-and-process" in text
    assert "# Steps" in text
    # Indexed.
    sk = await store.get("learned", "scrape-and-process")
    assert sk is not None
    assert sk.description.startswith("Fetch web content")
    # Audited.
    audit = await store.recent_audit_for_skill("scrape-and-process")
    assert len(audit) == 1
    assert audit[0].op == "create"
    assert audit[0].actor == "agent:synthesizer"
    assert audit[0].details.get("cluster_size") == 3


async def test_discover_skips_cluster_already_covered(synth_env) -> None:
    """Re-running discover on the same outcomes shouldn't duplicate the skill."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "scrape-and-process", "description": "x",
        "when_to_use": "y", "body": "z",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=_allow_gate(),
    )
    assert await synth.discover_new_skills() == 1
    # Second run — provider must NOT be called again.
    synth2 = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_ScriptedProvider(responses=[]), skills_root=root,
    )
    assert await synth2.discover_new_skills() == 0


async def test_discover_skips_below_size_threshold(synth_env) -> None:
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("read_file",), n=2)  # below min_size=3
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_ScriptedProvider(responses=[]), skills_root=root,
    )
    assert await synth.discover_new_skills() == 0


async def test_discover_handles_provider_failure_gracefully(synth_env) -> None:
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch",), n=3)

    class _BadProvider:
        model_name = "bad"
        async def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated provider down")

    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_BadProvider(), skills_root=root,
    )
    assert await synth.discover_new_skills() == 0
    # No skill written.
    assert await store.get("learned", "learned-web-fetch") is None


async def test_refine_rewrites_body_for_midtier(synth_env) -> None:
    db, root, store = synth_env
    # Seed an existing learned skill with mid-tier success_rate + parent_traces
    learned_dir = root / "learned" / "midtier-skill"
    learned_dir.mkdir(parents=True)
    body_original = "# Original\nDo the thing badly."
    manifest = SkillManifest(
        name="midtier-skill", description="d", when_to_use="w",
        source="learned", parent_traces=["t-mid-1"],
    )
    (learned_dir / "SKILL.md").write_text(
        f"---\nname: midtier-skill\ndescription: d\nwhen_to_use: w\nsource: learned\n"
        f"parent_traces: [t-mid-1]\n---\n\n{body_original}\n", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=learned_dir, body=body_original,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", "midtier-skill")
    assert sk is not None
    await store.set_success_rate(sk.skill_id, 0.6)
    # Bump n_executions ≥5
    for _ in range(5):
        await store.increment_n_executions(sk.skill_id)
    # Seed the parent trace's outcome so refine has context
    out_store = TaskOutcomeStore(db)
    await out_store.record(
        trace_id="t-mid-1", session_id="s", owl_name="scout", channel="cli",
        success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, step_durations={}, input_text="midtier task",
        response_text="midtier response",
    )
    out = await out_store.get_by_trace_id("t-mid-1")
    assert out is not None
    await out_store.set_quality_score(out.outcome_id, 0.6)

    provider = _ScriptedProvider(responses=[json.dumps({
        "body": "# Improved Body\nDo the thing well now.",
    })])
    synth = SkillSynthesizer(
        outcome_store=out_store, skill_store=store,
        provider=provider, skills_root=root, consent_gate=_allow_gate(),
    )
    n = await synth.refine_midtier_skills()
    assert n == 1
    updated_text = (learned_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "Improved Body" in updated_text
    audit = await store.recent_audit_for_skill("midtier-skill")
    ops = [e.op for e in audit]
    assert "update" in ops


async def test_deprecate_moves_low_performer_to_underscored_dir(synth_env) -> None:
    db, root, store = synth_env
    bad_dir = root / "learned" / "bad-skill"
    bad_dir.mkdir(parents=True)
    body = "# Bad\n"
    manifest = SkillManifest(
        name="bad-skill", description="d", when_to_use="w", source="learned",
    )
    (bad_dir / "SKILL.md").write_text(
        f"---\nname: bad-skill\ndescription: d\nwhen_to_use: w\nsource: learned\n"
        f"---\n\n{body}", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=bad_dir, body=body,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", "bad-skill")
    assert sk is not None
    await store.set_success_rate(sk.skill_id, 0.2)
    for _ in range(6):
        await store.increment_n_executions(sk.skill_id)

    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_ScriptedProvider(responses=[]), skills_root=root,
    )
    n = await synth.deprecate_low_performers()
    assert n == 1
    # Original dir is gone.
    assert not bad_dir.exists()
    # Moved under _deprecated/.
    moved = root / "learned" / "_deprecated" / "bad-skill"
    assert moved.exists()
    assert (moved / "SKILL.md").exists()
    # Index row removed (the loader will not re-discover _-prefixed dirs).
    assert await store.get("learned", "bad-skill") is None
    audit = await store.recent_audit_for_skill("bad-skill")
    assert any(e.op == "deprecate" for e in audit)


# ---------- Story 3.5: DNA completion_drive advisory nudge on deprecation --

async def _seed_learned_skill(
    store, root: Path, name: str, *, success_rate: float, n_executions: int = 6,
) -> None:
    """Seed a minimal learned skill directory + index row with a fixed
    success_rate/n_executions, matching the shape the existing deprecate
    tests build by hand."""
    skill_dir = root / "learned" / name
    skill_dir.mkdir(parents=True)
    body = f"# {name}\n"
    manifest = SkillManifest(name=name, description="d", when_to_use="w", source="learned")
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nwhen_to_use: w\nsource: learned\n"
        f"---\n\n{body}", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=skill_dir, body=body,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", name)
    assert sk is not None
    await store.set_success_rate(sk.skill_id, success_rate)
    for _ in range(n_executions):
        await store.increment_n_executions(sk.skill_id)


async def _deprecate_env(tmp_db: DbPool, tmp_path: Path, *, owl_dna: dict[str, float]):
    """synth_env-equivalent but with a wired OwlRegistry carrying named owls at
    given completion_drive values, for the DNA->deprecate advisory tests."""
    from stackowl.owls.dna import OwlDNA
    from stackowl.owls.manifest import OwlAgentManifest

    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    registry = OwlRegistry.with_default_secretary()
    for owl_name, drive in owl_dna.items():
        registry.register(
            OwlAgentManifest(
                name=owl_name, role="research", system_prompt="P", model_tier="fast",
                dna=OwlDNA(completion_drive=drive),
            )
        )
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=registry,
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    return registry, skills_root, components.store


def _make_synth(db: DbPool, root: Path, store, registry: OwlRegistry) -> SkillSynthesizer:
    return SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_ScriptedProvider(responses=[]), skills_root=root,
        owl_registry=registry, db=db,
    )


async def test_deprecate_high_completion_drive_owner_spares_borderline_skill(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """completion_drive=0.9 -> effective_threshold=0.368 (more lenient than the
    flat 0.4). A skill at success_rate=0.38 sits ABOVE the adjusted threshold
    but BELOW the flat one — proving the nudge genuinely changed the outcome
    (would deprecate under flat 0.4, does NOT under the advisory nudge)."""
    from stackowl.owls.skill_ownership import persist_skill_ownership

    registry, root, store = await _deprecate_env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    await _seed_learned_skill(store, root, "borderline-skill", success_rate=0.38)
    await persist_skill_ownership(tmp_db, "scout", "borderline-skill")

    synth = _make_synth(tmp_db, root, store, registry)
    n = await synth.deprecate_low_performers()
    assert n == 0
    assert (root / "learned" / "borderline-skill").exists()
    assert await store.get("learned", "borderline-skill") is not None


async def test_deprecate_low_completion_drive_owner_deprecates_sooner(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """completion_drive=0.1 -> effective_threshold=0.432 (stricter than the flat
    0.4). A skill at success_rate=0.42 sits BELOW the adjusted threshold but
    ABOVE the flat one — proving the nudge changed the outcome the other
    direction (would NOT deprecate under flat 0.4, DOES under the nudge)."""
    from stackowl.owls.skill_ownership import persist_skill_ownership

    registry, root, store = await _deprecate_env(tmp_db, tmp_path, owl_dna={"scout": 0.1})
    await _seed_learned_skill(store, root, "impatient-owner-skill", success_rate=0.42)
    await persist_skill_ownership(tmp_db, "scout", "impatient-owner-skill")

    synth = _make_synth(tmp_db, root, store, registry)
    n = await synth.deprecate_low_performers()
    assert n == 1
    assert not (root / "learned" / "impatient-owner-skill").exists()
    assert (root / "learned" / "_deprecated" / "impatient-owner-skill").exists()


async def test_deprecate_unowned_skill_uses_flat_threshold_unaffected(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """No owning owl -> unmodified _DEPRECATE_BELOW (0.4), regression: a skill
    with no ownership row behaves exactly as before this story."""
    registry, root, store = await _deprecate_env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    # Deliberately no persist_skill_ownership call — "unowned".
    await _seed_learned_skill(store, root, "unowned-skill", success_rate=0.39)

    synth = _make_synth(tmp_db, root, store, registry)
    n = await synth.deprecate_low_performers()
    assert n == 1
    assert (root / "learned" / "_deprecated" / "unowned-skill").exists()


async def test_deprecate_neutral_completion_drive_byte_identical_to_flat(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """completion_drive=0.5 (neutral/default) -> effective_threshold == 0.4
    exactly, byte-identical to the pre-story flat comparison."""
    from stackowl.owls.skill_ownership import persist_skill_ownership

    registry, root, store = await _deprecate_env(tmp_db, tmp_path, owl_dna={"scout": 0.5})
    await _seed_learned_skill(store, root, "neutral-owner-skill", success_rate=0.39)
    await persist_skill_ownership(tmp_db, "scout", "neutral-owner-skill")

    synth = _make_synth(tmp_db, root, store, registry)
    n = await synth.deprecate_low_performers()
    assert n == 1
    assert (root / "learned" / "_deprecated" / "neutral-owner-skill").exists()


async def test_deprecate_orphaned_ownership_row_degrades_without_crashing_run(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """An ownership row naming an owl that's no longer in the live registry
    (orphan) must degrade THAT skill to the flat threshold — not raise, and
    not abort the rest of the deprecate pass for other candidate skills."""
    from stackowl.owls.skill_ownership import persist_skill_ownership

    registry, root, store = await _deprecate_env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    await _seed_learned_skill(store, root, "orphan-owned-skill", success_rate=0.39)
    # "ghost" was never registered -> attach_skill_to_owl's live overlay no-ops,
    # but the durable row still exists (persist_skill_ownership doesn't require
    # live registration) -- exactly the orphaned-row shape this test targets.
    await persist_skill_ownership(tmp_db, "ghost", "orphan-owned-skill")
    # A second, normally-owned candidate to prove the run doesn't abort.
    await _seed_learned_skill(store, root, "sibling-skill", success_rate=0.39)
    await persist_skill_ownership(tmp_db, "scout", "sibling-skill")

    synth = _make_synth(tmp_db, root, store, registry)
    n = await synth.deprecate_low_performers()
    # Orphan degrades to flat 0.4 (0.39 < 0.4 -> deprecated); sibling's owner
    # (drive=0.9 -> threshold 0.368) leaves 0.39 undeprecated. Both processed,
    # neither raised.
    assert n == 1
    assert (root / "learned" / "_deprecated" / "orphan-owned-skill").exists()
    assert (root / "learned" / "sibling-skill").exists()


async def test_deprecate_one_gating_untouched_by_threshold_change(synth_env) -> None:
    """Regression: _deprecate_one's own mechanics (direct move + audit_write,
    no security-scan/consent-gate involved) are unchanged by this story — same
    assertions as the pre-existing flat-threshold deprecate test."""
    db, root, store = synth_env
    await _seed_learned_skill(store, root, "bad-skill-2", success_rate=0.2)

    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=_ScriptedProvider(responses=[]), skills_root=root,
    )
    n = await synth.deprecate_low_performers()
    assert n == 1
    assert not (root / "learned" / "bad-skill-2").exists()
    moved = root / "learned" / "_deprecated" / "bad-skill-2"
    assert moved.exists()
    assert (moved / "SKILL.md").exists()
    assert await store.get("learned", "bad-skill-2") is None
    audit = await store.recent_audit_for_skill("bad-skill-2")
    assert any(e.op == "deprecate" for e in audit)


async def test_synth_attaches_skill_to_owning_owl(tmp_db: DbPool, tmp_path: Path) -> None:
    """PA4b: discover attaches the learned skill to the owl that ran the cluster
    (live manifest.skills) AND records it durably (skill_ownership row)."""
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.owls.skill_ownership import read_all_skill_ownership

    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    registry = OwlRegistry.with_default_secretary()
    registry.register(
        OwlAgentManifest(name="scout", role="research", system_prompt="P", model_tier="fast")
    )
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=registry,
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    store = components.store
    # _seed_outcomes records owl_name="scout" → scout is the owning owl.
    await _seed_outcomes(tmp_db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "scrape-and-process", "description": "x",
        "when_to_use": "y", "body": "# Steps\n1. go",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(tmp_db), skill_store=store,
        provider=provider, skills_root=skills_root,
        owl_registry=registry, db=tmp_db, consent_gate=_allow_gate(),
        lookback_days=30, min_cluster_size=3, min_mean_quality=0.75,
    )
    assert await synth.discover_new_skills() == 1
    # Live: the owning owl's manifest now records ownership (injection-reachable).
    assert "scrape-and-process" in registry.get("scout").skills
    # Durable: a skill_ownership row exists so it survives restart.
    owned = await read_all_skill_ownership(tmp_db)
    assert "scrape-and-process" in owned.get("scout", [])


async def test_run_all_aggregates_counts(synth_env) -> None:
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "combined", "description": "x", "when_to_use": "y", "body": "z",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=_allow_gate(),
    )
    report = await synth.run_all()
    assert report.created == 1
    assert report.refined == 0
    assert report.deprecated == 0


# ---------- Task 18: per-model provider config — model threading -----------

async def test_discover_threads_constructor_model_to_provider_complete(synth_env) -> None:
    """SkillSynthesizer(model=...) must forward that exact model string into
    the DISCOVER phase's internal provider call (``_synthesize_one``), not the
    hardcoded ``model=""`` default.

    Genuinely discriminating: if the constructor kept ignoring ``model=`` and
    ``_synthesize_one`` kept hardcoding ``model=""``, ``seen_models`` would be
    ``[""]`` instead of the sentinel value below.
    """
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "discover-model-threaded", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, model="discover-resolved-model", skills_root=root,
        consent_gate=_allow_gate(),
        lookback_days=30, min_cluster_size=3, min_mean_quality=0.75,
    )
    n = await synth.discover_new_skills()
    assert n == 1
    assert provider.seen_models == ["discover-resolved-model"], (
        f"expected provider.complete to receive the constructor model, got: {provider.seen_models!r}"
    )


async def test_discover_default_model_is_empty_string_when_unset(synth_env) -> None:
    """Additive/byte-identical guarantee: no ``model=`` passed to the
    constructor -> the discover phase's provider call still receives
    ``model=""``, matching pre-Task-18 behavior exactly."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "discover-default-model", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=_allow_gate(),
        lookback_days=30, min_cluster_size=3, min_mean_quality=0.75,
    )
    n = await synth.discover_new_skills()
    assert n == 1
    assert provider.seen_models == [""]


async def test_refine_threads_constructor_model_to_provider_complete(synth_env) -> None:
    """SkillSynthesizer(model=...) must ALSO forward that exact model string
    into the REFINE phase's internal provider call (``_refine_one``) — a
    separate call site from discover's ``_synthesize_one``, so this must be
    proven independently rather than assumed from the discover-phase test."""
    db, root, store = synth_env
    learned_dir = root / "learned" / "midtier-skill"
    learned_dir.mkdir(parents=True)
    body_original = "# Original\nDo the thing badly."
    manifest = SkillManifest(
        name="midtier-skill", description="d", when_to_use="w",
        source="learned", parent_traces=["t-mid-1"],
    )
    (learned_dir / "SKILL.md").write_text(
        f"---\nname: midtier-skill\ndescription: d\nwhen_to_use: w\nsource: learned\n"
        f"parent_traces: [t-mid-1]\n---\n\n{body_original}\n", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=learned_dir, body=body_original,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", "midtier-skill")
    assert sk is not None
    await store.set_success_rate(sk.skill_id, 0.6)
    for _ in range(5):
        await store.increment_n_executions(sk.skill_id)
    out_store = TaskOutcomeStore(db)
    await out_store.record(
        trace_id="t-mid-1", session_id="s", owl_name="scout", channel="cli",
        success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, step_durations={}, input_text="midtier task",
        response_text="midtier response",
    )
    out = await out_store.get_by_trace_id("t-mid-1")
    assert out is not None
    await out_store.set_quality_score(out.outcome_id, 0.6)

    provider = _ScriptedProvider(responses=[json.dumps({
        "body": "# Improved Body\nDo the thing well now.",
    })])
    synth = SkillSynthesizer(
        outcome_store=out_store, skill_store=store,
        provider=provider, model="refine-resolved-model", skills_root=root,
        consent_gate=_allow_gate(),
    )
    n = await synth.refine_midtier_skills()
    assert n == 1
    assert provider.seen_models == ["refine-resolved-model"], (
        f"expected provider.complete to receive the constructor model, got: {provider.seen_models!r}"
    )


# ---------- Task 4: shared skill-authoring gate (bypass fix) ---------------

def test_resolve_consent_identity_uses_scheduled_identity_without_trace_context() -> None:
    """Reviewer Finding 1a: outside an interactive TraceContext (the genuinely
    unattended scheduled job's situation), resolve_consent_identity must
    return the SCHEDULED identity + the background-job fallback channel/session
    — never the live one."""
    from stackowl.skills.authoring import resolve_consent_identity

    tool_name, channel, session_id = resolve_consent_identity(
        live_tool_name="live-x", scheduled_tool_name="scheduled-y",
    )
    assert tool_name == "scheduled-y"
    assert channel == "scheduler"
    assert session_id == "scheduler"


def test_resolve_consent_identity_uses_live_identity_inside_interactive_trace() -> None:
    """Reviewer Finding 1a: inside an interactive TraceContext (a live human
    turn), resolve_consent_identity must read the REAL channel/session_id off
    TraceContext.get() and return the LIVE identity — the specific branch that
    fixes the SynthesizeSkillsTool regression, previously untested."""
    from stackowl.infra.trace import TraceContext
    from stackowl.skills.authoring import resolve_consent_identity

    token = TraceContext.start("live-session-1", interactive=True, channel="telegram")
    try:
        tool_name, channel, session_id = resolve_consent_identity(
            live_tool_name="live-x", scheduled_tool_name="scheduled-y",
        )
    finally:
        TraceContext.reset(token)
    assert tool_name == "live-x"
    assert channel == "telegram"
    assert session_id == "live-session-1"


async def test_scheduled_write_auto_trusted_via_real_consent_assembly(synth_env) -> None:
    """Reviewer Finding 2 (user decision): the scheduled identity is seeded
    with TrustTier.AUTO by the REAL ConsentAssembly.build — the daily job must
    still actually write with NO human prompt ever consulted, while
    security_scan_gate (not mocked here) still runs for real."""
    from unittest.mock import MagicMock

    from stackowl.tools.consent_assembly import ConsentAssembly

    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "auto-trusted-skill", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])

    components = ConsentAssembly.build(MagicMock())

    async def _boom(_req: object) -> None:
        raise AssertionError("prompter must NOT be consulted for an AUTO-tiered identity")

    components.routing_prompter.prompt = _boom  # type: ignore[method-assign]

    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=components.consent_gate,
    )
    n = await synth.discover_new_skills()
    assert n == 1
    assert (root / "learned" / "auto-trusted-skill" / "SKILL.md").exists()


async def test_synthesize_one_calls_scan_and_consent_before_write(synth_env) -> None:
    """The gate order must hold: security_scan_gate runs, THEN consent, and
    BOTH run before the real skill directory is ever created on disk."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "gate-order-skill", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])
    target = root / "learned" / "gate-order-skill"

    import stackowl.skills.authoring as authoring_mod
    real_scan = authoring_mod.security_scan_gate
    call_order: list[str] = []

    def _spy_scan(path):  # type: ignore[no-untyped-def]
        call_order.append("scan")
        assert not target.exists(), "security_scan_gate must run BEFORE the skill dir exists"
        return real_scan(path)

    monkeypatch_scan = authoring_mod.security_scan_gate
    authoring_mod.security_scan_gate = _spy_scan  # type: ignore[assignment]
    try:
        gate = _RecordingConsentGate(allow=True)
        real_request = gate.request

        async def _spy_request(**kwargs: object) -> bool:
            call_order.append("consent")
            assert call_order[0] == "scan", "consent must be consulted AFTER the scan"
            assert not target.exists(), "consent must be consulted BEFORE the write"
            return await real_request(**kwargs)

        gate.request = _spy_request  # type: ignore[method-assign]

        synth = SkillSynthesizer(
            outcome_store=TaskOutcomeStore(db), skill_store=store,
            provider=provider, skills_root=root, consent_gate=gate,
        )
        n = await synth.discover_new_skills()
    finally:
        authoring_mod.security_scan_gate = monkeypatch_scan  # type: ignore[assignment]

    assert n == 1
    assert call_order == ["scan", "consent"], call_order
    assert len(gate.calls) == 1
    assert gate.calls[0]["tool_name"] == _CONSENT_TOOL_NAME_SCHEDULED
    assert (target / "SKILL.md").exists()


async def test_synthesize_one_denied_consent_writes_nothing(synth_env) -> None:
    """A DENIED consent must leave NO file on disk and NEVER call store.upsert."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "denied-skill", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])
    gate = _RecordingConsentGate(allow=False)
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root, consent_gate=gate,
    )
    n = await synth.discover_new_skills()
    assert n == 0
    # Consent WAS consulted (the bug this task fixes is that it never was)...
    assert len(gate.calls) == 1
    # ...but nothing was written or indexed as a result of the denial.
    assert not (root / "learned" / "denied-skill").exists()
    assert await store.get("learned", "denied-skill") is None
    audit = await store.recent_audit_for_skill("denied-skill")
    assert audit == []


async def test_synthesize_one_no_gate_wired_fails_closed(synth_env) -> None:
    """consent_gate=None (the default) must ALSO refuse — never silently allow."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "no-gate-skill", "description": "d", "when_to_use": "w", "body": "z",
    })])
    synth = SkillSynthesizer(
        outcome_store=TaskOutcomeStore(db), skill_store=store,
        provider=provider, skills_root=root,  # consent_gate defaults to None
    )
    n = await synth.discover_new_skills()
    assert n == 0
    assert not (root / "learned" / "no-gate-skill").exists()
    assert await store.get("learned", "no-gate-skill") is None


async def test_synthesize_one_security_scan_blocks_before_consent(synth_env) -> None:
    """A blocked security scan must short-circuit BEFORE consent is even asked,
    and (like a denied consent) must leave nothing written or indexed."""
    db, root, store = synth_env
    await _seed_outcomes(db, sequence=("web_fetch", "shell"), n=3)
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "scan-blocked-skill", "description": "d", "when_to_use": "w",
        "body": "# Steps\n1. go",
    })])

    import stackowl.skills.authoring as authoring_mod
    original_scan = authoring_mod.security_scan_gate
    authoring_mod.security_scan_gate = lambda _path: (False, "simulated dangerous verdict")  # type: ignore[assignment]
    try:
        gate = _RecordingConsentGate(allow=True)
        synth = SkillSynthesizer(
            outcome_store=TaskOutcomeStore(db), skill_store=store,
            provider=provider, skills_root=root, consent_gate=gate,
        )
        n = await synth.discover_new_skills()
    finally:
        authoring_mod.security_scan_gate = original_scan  # type: ignore[assignment]

    assert n == 0
    assert gate.calls == [], "consent must never be consulted once the scan blocks"
    assert not (root / "learned" / "scan-blocked-skill").exists()
    assert await store.get("learned", "scan-blocked-skill") is None


async def test_refine_one_denied_consent_leaves_existing_skill_untouched(synth_env) -> None:
    """Refine's gated write must ALSO deny-by-default: the pre-existing body
    must be left byte-for-byte unchanged and no update audit row appended."""
    db, root, store = synth_env
    learned_dir = root / "learned" / "midtier-skill"
    learned_dir.mkdir(parents=True)
    body_original = "# Original\nDo the thing badly."
    manifest = SkillManifest(
        name="midtier-skill", description="d", when_to_use="w",
        source="learned", parent_traces=["t-mid-1"],
    )
    original_text = (
        f"---\nname: midtier-skill\ndescription: d\nwhen_to_use: w\nsource: learned\n"
        f"parent_traces: [t-mid-1]\n---\n\n{body_original}\n"
    )
    (learned_dir / "SKILL.md").write_text(original_text, encoding="utf-8")
    await store.upsert(LoadedSkill(
        manifest=manifest, path=learned_dir, body=body_original,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", "midtier-skill")
    assert sk is not None
    await store.set_success_rate(sk.skill_id, 0.6)
    for _ in range(5):
        await store.increment_n_executions(sk.skill_id)
    out_store = TaskOutcomeStore(db)
    await out_store.record(
        trace_id="t-mid-1", session_id="s", owl_name="scout", channel="cli",
        success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, step_durations={}, input_text="midtier task",
        response_text="midtier response",
    )

    provider = _ScriptedProvider(responses=[json.dumps({
        "body": "# Improved Body\nDo the thing well now.",
    })])
    gate = _RecordingConsentGate(allow=False)
    synth = SkillSynthesizer(
        outcome_store=out_store, skill_store=store,
        provider=provider, skills_root=root, consent_gate=gate,
    )
    n = await synth.refine_midtier_skills()
    assert n == 0
    assert len(gate.calls) == 1
    assert (learned_dir / "SKILL.md").read_text(encoding="utf-8") == original_text
    audit = await store.recent_audit_for_skill("midtier-skill")
    assert audit == []


# ---------- Task-4: Verification section guarantee -------------------------

def test_prompt_system_mentions_verification() -> None:
    """The author prompt must instruct the model to include a Verification section."""
    system = SkillSynthesizerPromptBuilder._SYSTEM
    assert "Verification" in system, (
        "_SYSTEM prompt must mention 'Verification' so the model knows to include it"
    )


def test_parse_new_skill_response_idempotent_when_verification_present() -> None:
    """When the model body already contains ## Verification, do not append another one."""
    body_with_verification = (
        "## Steps\n1. Do the thing.\n\n"
        "## Verification\nCheck the output matches the goal.\n\n"
        "## Pitfalls\nDon't skip step 1."
    )
    raw = json.dumps({
        "name": "has-verification",
        "description": "a skill with verification",
        "when_to_use": "always",
        "body": body_with_verification,
    })
    parsed = parse_new_skill_response(raw)
    assert parsed is not None
    body = parsed["body"]
    count = body.count("## Verification")
    assert count == 1, f"Expected exactly 1 '## Verification' heading, got {count}"


def test_parse_new_skill_response_appends_default_verification_when_absent() -> None:
    """When the model omits ## Verification, a default section is appended (fail-open)."""
    body_without_verification = (
        "## Steps\n1. Fetch the page.\n2. Shell-process the content.\n\n"
        "## Pitfalls\nMind the rate-limits."
    )
    raw = json.dumps({
        "name": "no-verification",
        "description": "a skill without verification",
        "when_to_use": "when needed",
        "body": body_without_verification,
    })
    parsed = parse_new_skill_response(raw)
    assert parsed is not None, "Skill must not be dropped (fail-open)"
    body = parsed["body"]
    assert "## Verification" in body, "Default Verification section must be appended"
    count = body.count("## Verification")
    assert count == 1, f"Expected exactly 1 '## Verification' heading, got {count}"
    # The default text from the constant must be present.
    assert _DEFAULT_VERIFICATION_SECTION.strip() in body


# ---------- Finding 1: REFINE path must also guarantee ## Verification ------

def test_parse_refined_body_appends_verification_when_absent() -> None:
    """parse_refined_body must append the default Verification section fail-open
    when the LLM-refined body omits it — same guarantee as the new-skill path."""
    raw = json.dumps({"body": "## Steps\n1. Do the thing.\n\n## Pitfalls\nMind the edge cases."})
    body = parse_refined_body(raw)
    assert body is not None, "Skill must not be dropped (fail-open)"
    assert _VERIFICATION_HEADING in body, "Default Verification section must be appended"
    count = body.count(_VERIFICATION_HEADING)
    assert count == 1, f"Expected exactly 1 '{_VERIFICATION_HEADING}' heading, got {count}"
    assert _DEFAULT_VERIFICATION_SECTION.strip() in body


def test_parse_refined_body_idempotent_when_verification_present() -> None:
    """parse_refined_body must NOT append a second ## Verification when one is already present."""
    body_with = (
        "## Steps\n1. Fetch.\n\n"
        "## Verification\nCheck the output.\n\n"
        "## Pitfalls\nDon't break things."
    )
    raw = json.dumps({"body": body_with})
    body = parse_refined_body(raw)
    assert body is not None
    count = body.count(_VERIFICATION_HEADING)
    assert count == 1, f"Expected exactly 1 '{_VERIFICATION_HEADING}' heading, got {count}"


# ---------- Finding 2: case-insensitive heading detection -------------------

def test_parse_new_skill_response_case_insensitive_detection() -> None:
    """A model-emitted '## verification' (lowercase) must NOT cause a double-append."""
    body_lowercase = "## Steps\n1. Do it.\n\n## verification\nCheck carefully.\n"
    raw = json.dumps({
        "name": "lower-verification",
        "description": "tests case sensitivity",
        "when_to_use": "always",
        "body": body_lowercase,
    })
    parsed = parse_new_skill_response(raw)
    assert parsed is not None
    body = parsed["body"]
    # Must not append another section on top of the lowercase one.
    assert body.lower().count("## verification") == 1, (
        "Detection must be case-insensitive — lowercase '## verification' should be recognised"
    )


def test_parse_refined_body_case_insensitive_detection() -> None:
    """A model-emitted '## verification' (lowercase) must NOT cause a double-append
    on the refine path either."""
    body_lowercase = "## Steps\n1. Do it.\n\n## verification\nCheck carefully.\n"
    raw = json.dumps({"body": body_lowercase})
    body = parse_refined_body(raw)
    assert body is not None
    assert body.lower().count("## verification") == 1, (
        "Refine path detection must be case-insensitive"
    )


def test_refine_prompt_mentions_verification() -> None:
    """build_for_refine's system prompt must instruct the model to include/preserve
    ## Steps / ## Verification / ## Pitfalls with Verification mandatory."""
    # Build a minimal Skill-like object — use a real Skill dataclass from the store.
    import time

    from stackowl.skills.store import Skill

    sk = Skill(
        skill_id=1,
        name="test-skill",
        description="a test",
        when_to_use="always",
        source="learned",
        version="0.1.0",
        body_text="## Steps\n1. Go.\n\n## Verification\nCheck.\n",
        path="/tmp/test-skill",
        manifest_json={"name": "test-skill", "description": "a test", "when_to_use": "always",
                       "source": "learned", "version": "0.1.0"},
        enabled=True,
        success_rate=0.6,
        n_executions=5,
        parent_traces=[],
        embedding=None,
        embedding_model=None,
        summary=None,
        summary_source=None,
        summary_body_hash=None,
        tool_names=(),
        loaded_at=time.time(),
        updated_at=time.time(),
    )
    msgs = SkillSynthesizerPromptBuilder().build_for_refine(sk, [])
    system_content = msgs[0].content
    assert "Verification" in system_content, (
        "build_for_refine system prompt must mention 'Verification' so the model preserves it"
    )
