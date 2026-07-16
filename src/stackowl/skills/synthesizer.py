"""SkillSynthesizer — agent learning loop: discover/refine/deprecate skills.

Per Learning Commit 3, sub-phase 3c. Three responsibilities, all driven by
``task_outcomes`` + ``reflections`` data accumulated since Commits 1-2:

1. **Discover** — cluster successful outcomes (quality_score ≥ 0.75) by
   their exact ``tool_sequence``. When ≥3 outcomes share a sequence,
   ask the fast-tier LLM to propose a NEW SKILL.md and write it to
   ``learned/<proposed-name>/``.

2. **Refine** — for learned skills with mid-tier performance (success_rate
   in [0.5, 0.7] and n_executions ≥5), ask the LLM to refine the recipe
   body in-place. Rewrites only the body; frontmatter preserved.

3. **Deprecate** — for learned skills with success_rate <0.4 and
   n_executions ≥5, move the directory to ``learned/_deprecated/<name>/``.
   Loader skips ``_``-prefixed dirs so they vanish from /skill list and
   the system prompt while staying on disk for forensics.

LLM-as-author calls use :func:`parse_json_response` (shared with Commit 2's
critic / reflection) for response validation.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from stackowl.commands.skill_helpers import hash_dir, snapshot_dir
from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.memory.json_parser import parse_json_response
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
from stackowl.providers.base import Message, ModelProvider
from stackowl.skills.authoring import (
    SkillWriteRequest,
    gated_skill_write,
    resolve_consent_identity,
)
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import Skill, SkillIndexStore

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.owls.registry import OwlRegistry
    from stackowl.skills.loader import LoadedSkill
    from stackowl.tools.registry import ConsequentialActionGate

# Consent-policy identities for every gated write this module makes (Task 4 /
# bypass fix; split per reviewer Finding 2). LIVE is used when an interactive
# turn is in flight (e.g. the synthesize_skills tool — a human is present and
# already passed the OUTER consequential check for that tool call) and stays
# on normal ALWAYS_ASK consent. SCHEDULED is used for the genuinely unattended
# daily job (no human to ask, ever) and is seeded with TrustTier.AUTO in
# ConsentAssembly.build — security_scan_gate still runs either way; AUTO only
# skips the human PROMPT. See resolve_consent_identity().
_CONSENT_TOOL_NAME_LIVE = "skill_synthesizer"
_CONSENT_TOOL_NAME_SCHEDULED = "skill_synthesizer_scheduled"

# ---------- clustering ------------------------------------------------------

_MIN_CLUSTER_SIZE_DEFAULT = 3
_MIN_MEAN_QUALITY_DEFAULT = 0.75
_LOOKBACK_DAYS_DEFAULT = 14
_REFINE_RANGE: tuple[float, float] = (0.5, 0.7)
_DEPRECATE_BELOW = 0.4
_MIN_EXECUTIONS_FOR_RATE = 5
_SECONDS_PER_DAY = 86_400
_SAMPLE_LIMIT_PER_CLUSTER = 5  # how many trace samples to include in the LLM prompt
_DEPRECATED_DIR_NAME = "_deprecated"


@dataclass(frozen=True)
class ToolSequenceCluster:
    """A group of outcomes that share the exact same tool sequence."""

    sequence: tuple[str, ...]
    outcomes: tuple[TaskOutcome, ...]

    @property
    def size(self) -> int:
        return len(self.outcomes)

    @property
    def mean_quality(self) -> float:
        scores = [o.quality_score for o in self.outcomes if o.quality_score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def proposed_slug(self) -> str:
        """Stable filesystem-safe slug derived from the sequence (used pre-LLM)."""
        joined = "-".join(self.sequence)
        # Match SkillManifest name pattern: ^[a-z][a-z0-9_-]*$ — strip the rest.
        slug = re.sub(r"[^a-z0-9_-]", "-", joined.lower()).strip("-")
        if not slug or not slug[0].isalpha():
            slug = f"learned-{slug}".strip("-")
        return slug[:40]


def _owning_owl(cluster: ToolSequenceCluster) -> str | None:
    """The owl that ran this cluster: most frequent non-empty ``owl_name``.

    Ties resolve to the first owl in cluster order (``Counter`` preserves
    insertion order and ``most_common`` is a stable sort). Returns ``None`` when
    no outcome carries an owl_name.
    """
    names = [o.owl_name for o in cluster.outcomes if o.owl_name]
    if not names:
        return None
    return Counter(names).most_common(1)[0][0]


def cluster_outcomes_by_tool_sequence(
    outcomes: list[TaskOutcome],
    *,
    min_size: int = _MIN_CLUSTER_SIZE_DEFAULT,
    min_mean_quality: float = _MIN_MEAN_QUALITY_DEFAULT,
) -> list[ToolSequenceCluster]:
    """Bucket ``outcomes`` by exact ``tool_sequence`` and filter by thresholds.

    Order matters: ``(web_fetch, shell)`` is a different cluster from
    ``(shell, web_fetch)``. Empty-sequence outcomes are dropped (no tools used →
    nothing to crystallize as a procedural skill).
    """
    log.skills.debug(
        "[synth] cluster: entry",
        extra={"_fields": {
            "n_outcomes": len(outcomes),
            "min_size": min_size, "min_mean_quality": min_mean_quality,
        }},
    )
    buckets: dict[tuple[str, ...], list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        if not o.tool_sequence:
            continue
        buckets[o.tool_sequence].append(o)
    clusters: list[ToolSequenceCluster] = []
    for seq, members in buckets.items():
        if len(members) < min_size:
            continue
        cluster = ToolSequenceCluster(sequence=seq, outcomes=tuple(members))
        if cluster.mean_quality < min_mean_quality:
            continue
        clusters.append(cluster)
    # Sort by (size desc, mean_quality desc) so the strongest patterns synth first.
    clusters.sort(key=lambda c: (c.size, c.mean_quality), reverse=True)
    log.skills.debug(
        "[synth] cluster: exit",
        extra={"_fields": {"n_clusters": len(clusters)}},
    )
    return clusters


# ---------- LLM-as-author prompt + parser -----------------------------------


# Default Verification section appended when the LLM omits one.
# Kept as a module-level constant so tests can assert its exact text.
_DEFAULT_VERIFICATION_SECTION = (
    "## Verification\n"
    "Before claiming the task is done, re-check the result against the original goal "
    "and cite concrete evidence (command output, file contents, API response, etc.). "
    "If the outcome does not match the goal, say so honestly and describe what failed."
)

_VERIFICATION_HEADING = "## Verification"


class SkillSynthesizerPromptBuilder:
    """Build the prompt that asks an LLM to author a new SKILL.md."""

    _SYSTEM = (
        "You are an expert author of agent skill libraries (Voyager-style). "
        "Given a cluster of past tasks where the agent used the SAME sequence "
        "of tools and produced good outcomes, write a reusable Skill that "
        "captures the playbook so the agent can apply it next time it sees "
        "the same shape of task.\n\n"
        "Structure the body with these sections in order:\n"
        "  ## Steps — numbered, concrete actions the agent takes\n"
        "  ## Verification — how to confirm success with evidence BEFORE claiming done\n"
        "  ## Pitfalls — common failure modes and how to avoid them\n\n"
        "The ## Verification section is MANDATORY. It must tell the agent how to "
        "confirm the task succeeded with concrete evidence (command output, file "
        "contents, API response, etc.) and what to say if it failed.\n\n"
        "Respond ONLY with a JSON object of this exact shape:\n"
        "{\n"
        '  "name": "kebab-case-name (max 40 chars, lowercase, [a-z0-9_-])",\n'
        '  "description": "one sentence — when this skill applies",\n'
        '  "when_to_use": "trigger conditions — what the user query looks like",\n'
        '  "body": "markdown playbook with ## Steps, ## Verification, ## Pitfalls"\n'
        "}\n"
        "The body must be useful as raw markdown — NO frontmatter, NO triple-backtick fences.\n"
        "Keep body under 800 words. Be concrete and operational, not abstract."
    )

    def build_for_new(self, cluster: ToolSequenceCluster) -> list[Message]:
        """Prompt for proposing a brand-new skill from a tool-sequence cluster."""
        samples = cluster.outcomes[:_SAMPLE_LIMIT_PER_CLUSTER]
        sample_dicts = [
            {
                "input": o.input_text[:300],
                "response_preview": o.response_text[:300],
                "quality_score": o.quality_score,
                "latency_ms": int(o.latency_ms),
            }
            for o in samples
        ]
        user = (
            f"Tool sequence (in order): {list(cluster.sequence)}\n"
            f"Cluster size: {cluster.size} outcomes\n"
            f"Mean quality score: {cluster.mean_quality:.2f}\n\n"
            f"Sample traces:\n{json.dumps(sample_dicts, indent=2)}\n\n"
            f"Propose ONE new Skill that captures this pattern."
        )
        return [Message(role="system", content=self._SYSTEM),
                Message(role="user", content=user)]

    def build_for_refine(
        self, skill: Skill, recent_outcomes: list[TaskOutcome],
    ) -> list[Message]:
        """Prompt for refining an existing skill's recipe body in-place."""
        system = (
            "You are refining an existing agent Skill that's been performing "
            "in the mid tier (50-70% success). The frontmatter MUST stay the "
            "same. ONLY the body markdown changes.\n\n"
            "Preserve (or improve) the three-section structure:\n"
            "  ## Steps — numbered, concrete actions the agent takes\n"
            "  ## Verification — how to confirm success with evidence BEFORE claiming done\n"
            "  ## Pitfalls — common failure modes and how to avoid them\n\n"
            "The ## Verification section is MANDATORY. It must tell the agent how to "
            "confirm the task succeeded with concrete evidence (command output, file "
            "contents, API response, etc.) and what to say if it failed.\n\n"
            "Respond ONLY with JSON of this shape:\n"
            '{ "body": "new markdown body — keep useful parts, fix what fails" }\n'
            "Keep body under 800 words."
        )
        sample_dicts = [
            {
                "input": o.input_text[:200],
                "quality_score": o.quality_score,
                "succeeded": o.success,
            }
            for o in recent_outcomes[:_SAMPLE_LIMIT_PER_CLUSTER]
        ]
        user = (
            f"Skill: {skill.name}  (success_rate={skill.success_rate:.2f}, "
            f"n_executions={skill.n_executions})\n"
            f"Description: {skill.description}\n"
            f"When to use: {skill.when_to_use}\n\n"
            f"Current body:\n---\n{skill.body_text}\n---\n\n"
            f"Recent outcomes from runs that used this skill:\n"
            f"{json.dumps(sample_dicts, indent=2)}\n\n"
            f"Propose an improved body."
        )
        return [Message(role="system", content=system),
                Message(role="user", content=user)]


def parse_new_skill_response(raw: str) -> dict[str, str] | None:
    """Parse the LLM's new-skill JSON. Returns ``None`` if invalid.

    Guarantee (fail-open): if the parsed body lacks a ``## Verification``
    heading, a default Verification section is appended so every persisted
    learned skill carries the verification discipline of the native catalog.
    The skill is never dropped solely because the section is missing.
    """
    obj = parse_json_response(
        raw, required_keys=["name", "description", "when_to_use", "body"],
    )
    if obj is None:
        return None
    out = {k: str(obj.get(k, "")).strip() for k in ("name", "description", "when_to_use", "body")}
    if not out["name"] or not out["description"] or not out["body"]:
        return None
    # Coerce name to the SkillManifest pattern.
    name = re.sub(r"[^a-z0-9_-]", "-", out["name"].lower()).strip("-")
    if not name or not name[0].isalpha():
        return None
    out["name"] = name[:40]
    # 2. DECISION — ensure Verification section is present (idempotent, case-insensitive).
    if _VERIFICATION_HEADING.lower() not in out["body"].lower():
        log.skills.debug(
            "[synth] parse_new_skill_response: verification section absent — appending default",
            extra={"_fields": {"name": out["name"]}},
        )
        out["body"] = out["body"].rstrip() + "\n\n" + _DEFAULT_VERIFICATION_SECTION
    return out


def parse_refined_body(raw: str) -> str | None:
    """Parse the LLM's refined-body JSON. Returns ``None`` if invalid.

    Guarantee (fail-open): if the refined body lacks a ``## Verification``
    heading (case-insensitive), the same default section appended by
    :func:`parse_new_skill_response` is added — so old learned skills that
    predate the Verification patch keep the discipline after a refine pass.
    """
    obj = parse_json_response(raw, required_keys=["body"])
    if obj is None:
        return None
    body = str(obj.get("body", "")).strip()
    if not body:
        return None
    # 2. DECISION — ensure Verification section is present (idempotent, case-insensitive).
    if _VERIFICATION_HEADING.lower() not in body.lower():
        log.skills.debug(
            "[synth] parse_refined_body: verification section absent — appending default",
        )
        body = body.rstrip() + "\n\n" + _DEFAULT_VERIFICATION_SECTION
    return body


# ---------- the synthesizer -------------------------------------------------


@dataclass(frozen=True)
class SynthesisReport:
    """Aggregate counts returned to the caller (and into the JobResult)."""

    created: int
    refined: int
    deprecated: int


class SkillSynthesizer:
    """The orchestrator for discover + refine + deprecate phases."""

    def __init__(
        self,
        *,
        outcome_store: TaskOutcomeStore,
        skill_store: SkillIndexStore,
        provider: ModelProvider,
        skills_root: Path,
        embedding_registry: EmbeddingRegistry | None = None,
        owl_registry: OwlRegistry | None = None,
        db: DbPool | None = None,
        consent_gate: ConsequentialActionGate | None = None,
        lookback_days: int = _LOOKBACK_DAYS_DEFAULT,
        min_cluster_size: int = _MIN_CLUSTER_SIZE_DEFAULT,
        min_mean_quality: float = _MIN_MEAN_QUALITY_DEFAULT,
        max_new_per_run: int = 5,
        max_refine_per_run: int = 5,
    ) -> None:
        log.skills.debug(
            "[synth] init: ready",
            extra={"_fields": {
                "lookback_days": lookback_days,
                "min_cluster_size": min_cluster_size,
                "min_mean_quality": min_mean_quality,
            }},
        )
        self._outcomes = outcome_store
        self._skills = skill_store
        self._provider = provider
        self._root = skills_root
        self._embedding_registry = embedding_registry
        self._owl_registry = owl_registry
        self._db = db
        # Task 4 — every SKILL.md write routes through gated_skill_write(), which
        # fails closed (refuses) when consent_gate is None. No gate wired is
        # therefore "discover/refine writes nothing", never "write unguarded".
        self._consent_gate = consent_gate
        self._lookback_days = lookback_days
        self._min_cluster_size = min_cluster_size
        self._min_mean_quality = min_mean_quality
        self._max_new = max_new_per_run
        self._max_refine = max_refine_per_run
        self._prompts = SkillSynthesizerPromptBuilder()

    async def run_all(self) -> SynthesisReport:
        """Execute all three phases. Each is best-effort — one failing won't
        block the others. Returns aggregate counts."""
        # 1. ENTRY
        log.skills.info("[synth] run_all: entry")
        created = 0
        refined = 0
        deprecated = 0
        try:
            created = await self.discover_new_skills()
        except Exception as exc:  # B5
            log.skills.error("[synth] run_all: discover phase failed", exc_info=exc)
        try:
            refined = await self.refine_midtier_skills()
        except Exception as exc:  # B5
            log.skills.error("[synth] run_all: refine phase failed", exc_info=exc)
        try:
            deprecated = await self.deprecate_low_performers()
        except Exception as exc:  # B5
            log.skills.error("[synth] run_all: deprecate phase failed", exc_info=exc)
        # 4. EXIT
        log.skills.info(
            "[synth] run_all: exit",
            extra={"_fields": {
                "created": created, "refined": refined, "deprecated": deprecated,
            }},
        )
        return SynthesisReport(created=created, refined=refined, deprecated=deprecated)

    # ----- phase 1 — discover -----------------------------------------------

    async def discover_new_skills(self) -> int:
        """Cluster high-quality outcomes; write new SKILL.md for each cluster."""
        log.skills.debug("[synth] discover: entry")
        since = time.time() - self._lookback_days * _SECONDS_PER_DAY
        outcomes = await self._outcomes.list_successful_with_sequence(
            min_quality=self._min_mean_quality, since_epoch=since,
        )
        if not outcomes:
            log.skills.debug("[synth] discover: exit — no successful outcomes")
            return 0
        clusters = cluster_outcomes_by_tool_sequence(
            outcomes, min_size=self._min_cluster_size,
            min_mean_quality=self._min_mean_quality,
        )
        if not clusters:
            log.skills.debug("[synth] discover: exit — no qualifying clusters")
            return 0
        written = 0
        for cluster in clusters[: self._max_new]:
            if await self._cluster_already_covered(cluster):
                log.skills.debug(
                    "[synth] discover: skipping cluster — already covered",
                    extra={"_fields": {"sequence": list(cluster.sequence)}},
                )
                continue
            ok = await self._synthesize_one(cluster)
            if ok:
                written += 1
        log.skills.info(
            "[synth] discover: exit",
            extra={"_fields": {"clusters_total": len(clusters), "written": written}},
        )
        return written

    async def _cluster_already_covered(self, cluster: ToolSequenceCluster) -> bool:
        """True if some existing learned skill records this cluster's trace_ids."""
        traces = {o.trace_id for o in cluster.outcomes}
        learned = await self._skills.list_for_source("learned")
        return any(set(sk.parent_traces) & traces for sk in learned)

    async def _synthesize_one(self, cluster: ToolSequenceCluster) -> bool:
        """One LLM call → one new SKILL.md written + indexed + audited."""
        log.skills.debug(
            "[synth] synthesize_one: entry",
            extra={"_fields": {
                "sequence": list(cluster.sequence),
                "size": cluster.size, "mean_quality": cluster.mean_quality,
            }},
        )
        messages = self._prompts.build_for_new(cluster)
        try:
            completion = await self._provider.complete(messages, model="")
        except Exception as exc:  # B5
            log.skills.warning(
                "[synth] synthesize_one: provider call failed",
                exc_info=exc,
                extra={"_fields": {"sequence": list(cluster.sequence)}},
            )
            return False
        parsed = parse_new_skill_response(completion.content)
        if parsed is None:
            log.skills.warning(
                "[synth] synthesize_one: response unparseable — skipping",
                extra={"_fields": {"preview": completion.content[:200]}},
            )
            return False
        # Pick a not-yet-taken directory name. Just a path decision (no I/O
        # writes) — the real mkdir/write only happens inside gated_skill_write()
        # once BOTH the security scan and consent have passed.
        proposed_name = parsed["name"]
        target_dir = self._root / "learned" / proposed_name
        i = 1
        while target_dir.exists():
            target_dir = self._root / "learned" / f"{proposed_name}-{i}"
            i += 1
        final_name = target_dir.name
        try:
            manifest = SkillManifest(
                name=final_name,
                description=parsed["description"][:300],
                when_to_use=parsed["when_to_use"][:300],
                version="0.1.0",
                source="learned",
                parent_traces=[o.trace_id for o in cluster.outcomes[:10]],
            )
        except Exception as exc:  # B5
            log.skills.warning(
                "[synth] synthesize_one: SkillManifest validation failed — skipping",
                exc_info=exc, extra={"_fields": {"name": final_name}},
            )
            return False

        # Task 4 — security_scan_gate -> consent -> write -> store.upsert, all
        # via the shared gate (fixes the direct-write bypass). A blocked scan
        # or a denied consent leaves NOTHING on disk and NOTHING indexed.
        skill_md = _emit_skill_md(manifest, parsed["body"])
        tool_name, channel, session_id = resolve_consent_identity(
            live_tool_name=_CONSENT_TOOL_NAME_LIVE,
            scheduled_tool_name=_CONSENT_TOOL_NAME_SCHEDULED,
        )
        request = SkillWriteRequest(
            target_dir=target_dir, manifest=manifest, body=parsed["body"],
            skill_md_text=skill_md,
            consent_summary=(
                f"Auto-author new skill '{final_name}' from a "
                f"{cluster.size}-outcome success cluster "
                f"(tools: {', '.join(cluster.sequence)})"
            ),
            tool_name=tool_name, channel=channel, session_id=session_id,
        )
        result = await gated_skill_write(
            request, store=self._skills, consent_gate=self._consent_gate,
        )
        if not result.ok:
            log.skills.warning(
                "[synth] synthesize_one: gated write refused — skipping",
                extra={"_fields": {"name": final_name, "reason": result.reason}},
            )
            return False
        loaded = result.loaded
        assert loaded is not None  # gated_skill_write guarantees this on ok=True
        after_hash = hash_dir(target_dir)
        # PA4b — attach the learned skill to its OWNING owl (the owl that ran the
        # clustered tasks) so it becomes reachable on the injection/capability
        # path AND survives restart. Best-effort: a failed attach logs and leaves
        # the skill on disk unowned (no worse than before PA4b) — never aborts.
        # Runs AFTER the gated write (not before) so a denied/blocked write never
        # leaves a dangling ownership record for a skill that doesn't exist.
        owl_attached = await self._attach_to_owner(cluster, final_name)
        if owl_attached:
            loaded = replace(loaded, owls_registered=1)
            await self._skills.upsert(loaded)
        await self._embed_one_if_wired(loaded)
        await self._skills.audit_write(
            skill_name=final_name, source="learned", op="create",
            actor="agent:synthesizer", after_hash=after_hash,
            details={
                "tool_sequence": list(cluster.sequence),
                "cluster_size": cluster.size,
                "mean_quality": cluster.mean_quality,
                "parent_traces": manifest.parent_traces,
            },
            snapshot=snapshot_dir(target_dir),
        )
        log.skills.info(
            "[synth] synthesize_one: exit",
            extra={"_fields": {
                "name": final_name, "cluster_size": cluster.size,
                "mean_quality": cluster.mean_quality,
            }},
        )
        return True

    async def _attach_to_owner(
        self, cluster: ToolSequenceCluster, skill_name: str
    ) -> bool:
        """Attach *skill_name* to the cluster's owning owl (live + durable).

        Returns True only when the live overlay actually changed the manifest.
        No-ops (logged) when the owl_registry/db aren't wired or no owl owns the
        cluster. Wrapped in try/except — a failure leaves the skill unowned but
        never aborts synthesis.
        """
        owner = _owning_owl(cluster)
        if owner is None or self._owl_registry is None or self._db is None:
            log.skills.debug(
                "[synth] attach_to_owner: no-op",
                extra={"_fields": {
                    "skill": skill_name, "owner": owner,
                    "have_registry": self._owl_registry is not None,
                    "have_db": self._db is not None,
                }},
            )
            return False
        try:
            from stackowl.owls.skill_ownership import (
                attach_skill_to_owl,
                persist_skill_ownership,
            )

            attached = attach_skill_to_owl(self._owl_registry, owner, skill_name)
            # Persist regardless of the live result so the durable record exists
            # for the next boot even if the owl wasn't loaded this process
            # (idempotent upsert — boot hydrate then attaches it).
            await persist_skill_ownership(self._db, owner, skill_name)
            log.skills.info(
                "[synth] attach_to_owner: exit",
                extra={"_fields": {
                    "skill": skill_name, "owner": owner, "live_attached": attached,
                }},
            )
            return attached
        except Exception as exc:  # B5 — never abort synthesis on attach failure
            log.skills.warning(
                "[synth] attach_to_owner: failed — skill left unowned",
                exc_info=exc,
                extra={"_fields": {"skill": skill_name, "owner": owner}},
            )
            return False

    async def _purge_ownership(self, skill_name: str) -> None:
        """Drop a deleted skill's ownership (live detach + durable delete). Best-
        effort: a failure logs and leaves a stale row (the orphan-safe hydrator
        tolerates it) — never aborts deprecation."""
        if self._db is None:
            return
        try:
            from stackowl.owls.skill_ownership import purge_skill_ownership

            await purge_skill_ownership(
                self._db, skill_name, registry=self._owl_registry
            )
        except Exception as exc:  # B5 — never abort deprecation on purge failure
            log.skills.warning(
                "[synth] purge_ownership: failed — ownership row may linger",
                exc_info=exc, extra={"_fields": {"skill": skill_name}},
            )

    # ----- phase 2 — refine -------------------------------------------------

    async def refine_midtier_skills(self) -> int:
        """Rewrite the body of learned skills with mid-tier performance."""
        log.skills.debug("[synth] refine: entry")
        learned = await self._skills.list_for_source("learned")
        candidates = [
            s for s in learned
            if (
                s.enabled
                and s.success_rate is not None
                and _REFINE_RANGE[0] <= s.success_rate <= _REFINE_RANGE[1]
                and s.n_executions >= _MIN_EXECUTIONS_FOR_RATE
            )
        ]
        refined = 0
        for sk in candidates[: self._max_refine]:
            ok = await self._refine_one(sk)
            if ok:
                refined += 1
        log.skills.info(
            "[synth] refine: exit",
            extra={"_fields": {"candidates": len(candidates), "refined": refined}},
        )
        return refined

    async def _refine_one(self, skill: Skill) -> bool:
        log.skills.debug(
            "[synth] refine_one: entry",
            extra={"_fields": {"name": skill.name, "rate": skill.success_rate}},
        )
        # Pull a few of THIS skill's parent_traces' outcomes for context.
        recent: list[TaskOutcome] = []
        traces_raw = skill.manifest_json.get("parent_traces") or []
        traces: list[str] = list(traces_raw) if isinstance(traces_raw, list) else []
        for tid in traces:
            out = await self._outcomes.get_by_trace_id(str(tid))
            if out is not None:
                recent.append(out)
        messages = self._prompts.build_for_refine(skill, recent)
        try:
            completion = await self._provider.complete(messages, model="")
        except Exception as exc:  # B5
            log.skills.warning(
                "[synth] refine_one: provider call failed",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return False
        new_body = parse_refined_body(completion.content)
        if new_body is None:
            log.skills.warning(
                "[synth] refine_one: response unparseable — skipping",
                extra={"_fields": {"name": skill.name, "preview": completion.content[:200]}},
            )
            return False
        skill_dir = Path(skill.path)
        before = hash_dir(skill_dir)
        # Re-emit SKILL.md with preserved frontmatter + new body
        try:
            manifest = SkillManifest.model_validate(skill.manifest_json)
        except Exception as exc:  # B5
            log.skills.warning(
                "[synth] refine_one: cannot reconstruct manifest from index — skipping",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return False
        new_text = _emit_skill_md(manifest, new_body)

        # Task 4 — security_scan_gate -> consent -> write -> store.upsert, all
        # via the shared gate (fixes the direct-write bypass). A blocked scan
        # or a denied consent leaves the existing SKILL.md untouched.
        tool_name, channel, session_id = resolve_consent_identity(
            live_tool_name=_CONSENT_TOOL_NAME_LIVE,
            scheduled_tool_name=_CONSENT_TOOL_NAME_SCHEDULED,
        )
        request = SkillWriteRequest(
            target_dir=skill_dir, manifest=manifest, body=new_body,
            skill_md_text=new_text,
            consent_summary=(
                f"Auto-refine skill '{skill.name}' body "
                f"(success_rate={skill.success_rate:.2f}, "
                f"n_executions={skill.n_executions})"
            ),
            tool_name=tool_name, channel=channel, session_id=session_id,
        )
        result = await gated_skill_write(
            request, store=self._skills, consent_gate=self._consent_gate,
        )
        if not result.ok:
            log.skills.warning(
                "[synth] refine_one: gated write refused — skipping",
                extra={"_fields": {"name": skill.name, "reason": result.reason}},
            )
            return False
        loaded = result.loaded
        assert loaded is not None  # gated_skill_write guarantees this on ok=True
        after = hash_dir(skill_dir)
        await self._embed_one_if_wired(loaded)
        await self._skills.audit_write(
            skill_name=skill.name, source="learned", op="update",
            actor="agent:synthesizer", before_hash=before, after_hash=after,
            details={
                "phase": "refine",
                "success_rate_at_refine": skill.success_rate,
                "n_executions_at_refine": skill.n_executions,
            },
            snapshot=snapshot_dir(skill_dir),
        )
        log.skills.info(
            "[synth] refine_one: exit",
            extra={"_fields": {"name": skill.name}},
        )
        return True

    # ----- phase 3 — deprecate ----------------------------------------------

    async def _effective_deprecate_threshold(
        self, skill_name: str, skill_to_owls: dict[str, list[str]],
    ) -> float:
        """Bounded advisory nudge on ``_DEPRECATE_BELOW`` from the owning owl(s)'
        CURRENT ``completion_drive`` trait (AD-7 — additive weight on an existing
        threshold, never a new veto; Story 3.5, the DNA→skill mirror of 3.4's
        skill→DNA direction).

        ``effective = _DEPRECATE_BELOW * (1.0 - 0.2 * (avg_completion_drive - 0.5))``
        — a highly-persistent owl (drive→1.0) gets a more LENIENT (lower)
        threshold so a skill must be worse before it's deprecated; a
        low-persistence owl (drive→0.0) gets a stricter (higher) one. At the
        neutral default (0.5) this returns ``_DEPRECATE_BELOW`` unchanged.

        No owning owl, or ``owl_registry``/``db`` not wired → unmodified
        ``_DEPRECATE_BELOW`` (no signal, no adjustment — the same
        None-means-no-opinion convention Story 3.4 established). An orphaned
        ownership row (owl name no longer in the live registry) is skipped,
        not fatal — one bad row degrades to no-adjustment for this skill, it
        never aborts the run for other skills.
        """
        owners = skill_to_owls.get(skill_name, [])
        if not owners or self._owl_registry is None or self._db is None:
            return _DEPRECATE_BELOW
        drives: list[float] = []
        for owl_name in owners:
            try:
                drives.append(self._owl_registry.get(owl_name).dna.completion_drive)
            except OwlNotFoundError:
                log.skills.debug(
                    "[synth] effective_deprecate_threshold: orphaned ownership row — skipped",
                    extra={"_fields": {"skill": skill_name, "owl": owl_name}},
                )
        if not drives:
            return _DEPRECATE_BELOW
        avg_drive = sum(drives) / len(drives)
        return _DEPRECATE_BELOW * (1.0 - 0.2 * (avg_drive - 0.5))

    async def deprecate_low_performers(self) -> int:
        """Move chronically-failing learned skills under ``learned/_deprecated/``."""
        # 1. ENTRY
        log.skills.debug("[synth] deprecate: entry")
        skill_to_owls: dict[str, list[str]] = {}
        if self._db is not None:
            from stackowl.owls.skill_ownership import read_all_skill_ownership

            try:
                owl_to_skills = await read_all_skill_ownership(self._db)
            except Exception as exc:  # B5 — a DB hiccup degrades to flat-threshold
                # deprecation, matching hydrate_skill_ownership's own convention,
                # rather than propagating and skipping this entire phase.
                log.skills.warning(
                    "[synth] deprecate: ownership read failed — falling back "
                    "to flat threshold for all candidates",
                    exc_info=exc,
                )
                owl_to_skills = {}
            for owl_name, skill_names in owl_to_skills.items():
                for skill_name in skill_names:
                    skill_to_owls.setdefault(skill_name, []).append(owl_name)
        # 2. DECISION — the enabled/n_executions legs of the candidate filter are
        # unchanged; only the success_rate comparison now uses a per-skill
        # advisory threshold instead of the flat _DEPRECATE_BELOW constant.
        learned = await self._skills.list_for_source("learned")
        candidates: list[Skill] = []
        for s in learned:
            if not (
                s.enabled
                and s.success_rate is not None
                and s.n_executions >= _MIN_EXECUTIONS_FOR_RATE
            ):
                continue
            threshold = await self._effective_deprecate_threshold(s.name, skill_to_owls)
            if s.success_rate < threshold:
                candidates.append(s)
        moved = 0
        deprecated_root = self._root / "learned" / _DEPRECATED_DIR_NAME
        deprecated_root.mkdir(parents=True, exist_ok=True)
        for sk in candidates:
            ok = await self._deprecate_one(sk, deprecated_root)
            if ok:
                moved += 1
        # 4. EXIT
        log.skills.info(
            "[synth] deprecate: exit",
            extra={"_fields": {
                "candidates": len(candidates), "moved": moved,
                "owned_skills": len(skill_to_owls),
            }},
        )
        return moved

    async def _deprecate_one(self, skill: Skill, deprecated_root: Path) -> bool:
        log.skills.debug(
            "[synth] deprecate_one: entry",
            extra={"_fields": {"name": skill.name, "rate": skill.success_rate}},
        )
        src = Path(skill.path)
        if not src.exists():
            log.skills.warning(
                "[synth] deprecate_one: dir missing — pruning index only",
                extra={"_fields": {"name": skill.name, "path": str(src)}},
            )
            await self._skills.delete(skill.skill_id)
            await self._purge_ownership(skill.name)
            return False
        before = hash_dir(src)
        # Capture snapshot BEFORE the move so /skill restore can resurrect it.
        snapshot_before = snapshot_dir(src)
        target = deprecated_root / skill.name
        i = 1
        while target.exists():
            target = deprecated_root / f"{skill.name}-{i}"
            i += 1
        try:
            shutil.move(str(src), str(target))
        except Exception as exc:  # B5
            log.skills.warning(
                "[synth] deprecate_one: move failed",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return False
        # Drop the index row — the loader's _-prefix skip rule means the moved
        # dir won't be re-discovered next boot.
        await self._skills.delete(skill.skill_id)
        # PA4b — also drop ownership: detach live + delete the durable row, else the
        # boot hydrator re-attaches this now-dead skill to its owl forever.
        await self._purge_ownership(skill.name)
        await self._skills.audit_write(
            skill_name=skill.name, source="learned", op="deprecate",
            actor="agent:synthesizer", before_hash=before,
            details={
                "moved_to": str(target),
                "success_rate_at_deprecate": skill.success_rate,
                "n_executions_at_deprecate": skill.n_executions,
            },
            snapshot=snapshot_before,
        )
        log.skills.info(
            "[synth] deprecate_one: exit",
            extra={"_fields": {"name": skill.name, "moved_to": str(target)}},
        )
        return True


    async def _embed_one_if_wired(self, loaded: LoadedSkill) -> None:
        """Best-effort: embed a single just-written skill so it's immediately
        retrievable by classify (without waiting for next boot's load_all pass).

        Same shape as the assembly's batch ``_embed_missing`` — kept inline to
        avoid the cyclic import and because we know we want exactly ONE skill.
        """
        if self._embedding_registry is None:
            return
        try:
            from stackowl.skills.assembly import _embed_missing

            await _embed_missing([loaded], self._skills, self._embedding_registry)
        except Exception as exc:  # B5 — embedding is enhancement, not gating
            log.skills.warning(
                "[synth] _embed_one_if_wired: embed failed — skill saved unindexed",
                exc_info=exc, extra={"_fields": {"name": loaded.manifest.name}},
            )


def _emit_skill_md(manifest: SkillManifest, body: str) -> str:
    """Render a SKILL.md from frontmatter + body.

    Same shape as :func:`stackowl.skills.skill_md.parse_skill_md` accepts.
    """
    fm_dict = manifest.model_dump(mode="json", exclude_none=True)
    # Strip agent-managed fields from frontmatter — they belong in the index,
    # not on the user-visible file.
    for field in ("success_rate", "n_executions", "parent_traces", "embedding_model"):
        fm_dict.pop(field, None)
    fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False).rstrip("\n")
    return f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"
