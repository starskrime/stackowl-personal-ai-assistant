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
    _DEFAULT_VERIFICATION_SECTION,
    _VERIFICATION_HEADING,
    SkillSynthesizer,
    SkillSynthesizerPromptBuilder,
    ToolSequenceCluster,
    cluster_outcomes_by_tool_sequence,
    parse_new_skill_response,
    parse_refined_body,
)
from stackowl.tools.registry import ToolRegistry


@dataclass
class _ScriptedProvider:
    """Stub ModelProvider that returns canned strings in order."""

    responses: list[str]
    model_name: str = "stub-fast"

    def __post_init__(self) -> None:
        self.calls: list[list[Message]] = []
        self._idx = 0

    async def complete(self, messages: list[Message], model: str = "") -> CompletionResult:  # noqa: ARG002
        self.calls.append(list(messages))
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
        provider=provider, skills_root=root,
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
        provider=provider, skills_root=root,
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
        provider=provider, skills_root=root,
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
        owl_registry=registry, db=tmp_db,
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
        provider=provider, skills_root=root,
    )
    report = await synth.run_all()
    assert report.created == 1
    assert report.refined == 0
    assert report.deprecated == 0


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
