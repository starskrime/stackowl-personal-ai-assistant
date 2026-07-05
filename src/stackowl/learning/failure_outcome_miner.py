"""FailureOutcomeMiner — cluster FAILED ``TaskOutcome`` rows and author a
learned SKILL.md from a verified root-cause-analysis (RCA) verdict.

Sibling to :mod:`stackowl.learning.tool_outcome_miner`. Mirrors its
clustering/threshold SHAPE (bucket outcomes, gate on an evidence-count
threshold — see ``_MIN_EVIDENCE``) but targets the opposite half of the data:
``ToolOutcomeMiner.mine()`` explicitly SKIPS every failed outcome
(``if o.failure_class: continue`` — POSITIVE-ONLY LEARNING, an operator
directive that stays untouched by this module). This miner exists
specifically to look at the rows that positive-only mining discards, so a
recurring incident can eventually become a reusable fix skill instead of
silently repeating forever.

Data source — reads failures directly, not via the critic's scored queue
--------------------------------------------------------------------------
The critic (``CriticScorerHandler``) only ever scores SUCCESSFUL outcomes —
its work queue is ``TaskOutcomeStore.list_pending_critic()``, hard-restricted
to ``success = 1 AND failure_class IS NULL`` (POSITIVE-ONLY LEARNING, see the
F-51 note on that method). A failed outcome therefore NEVER gets
``quality_score`` set. This miner uses
:meth:`~stackowl.memory.outcome_store.TaskOutcomeStore.list_failed_global`,
which selects on ``failure_class IS NOT NULL`` directly — it does NOT require
``quality_score IS NOT NULL`` the way the success miner's
``list_scored_for_owl_global`` does. Reusing that method here would make
every failure permanently invisible to this miner, no matter how many piled
up.

Clustering grain — capability_tag, not raw tool_name
--------------------------------------------------------------------------
The plan brief describes bucketing by ``(capability_class, error_signature)``.
``TaskOutcome`` (:mod:`stackowl.memory.outcome_store`) carries neither field
by that name, but the platform already HAS a "capability class" concept:
``stackowl.pipeline.capability_substitution`` groups sibling tools (e.g.
``web_fetch`` and ``browser_browse``, both ``capability_tag="web_knowledge"``)
so the self-heal substitution layer treats them as interchangeable. Clustering
on raw ``tool_name`` would silently miss a 4-evidence incident split as 2
``web_fetch`` timeouts + 2 ``browser_browse`` timeouts. So this module groups
by each tool's ``capability_tag`` — resolved via an injected
``capability_tag_lookup(tool_name) -> tag | None`` callable (e.g. built from a
live ``ToolRegistry`` as ``lambda n: getattr(getattr(registry.get(n),
"manifest", None), "capability_tag", None)``, the same accessor
``capability_substitution.find_substitute`` uses) — rather than importing
``ToolRegistry`` directly, keeping this module runnable/testable standalone.

Fallback (documented, not silent): when no lookup is supplied, or a tool has
no registered ``capability_tag``, the raw ``tool_name`` is used as its own
capability key — a tool with no declared siblings is simply a "capability
class of one," which is the correct behavior, not a degraded one.
``error_signature`` -> ``failure_class`` (the exception class name derived by
``classify_failure`` in ``outcome_store.py``) needed no such lookup; it's used
directly.

RCA integration point (for Task 6: incident trigger + staged RCA, and Task 7:
consume RCA result): clustering + threshold logic lives here, standalone of
any RCA machinery. A cluster only becomes a SKILL.md when the caller supplies
a matching, ``verified=True`` :class:`RcaVerdict` for its
``(capability_class, failure_class)`` key. Task 6/7 will eventually produce
these from a completed RCA session; for THIS task they are hand-built (see
tests).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from stackowl.infra.observability import log
from stackowl.skills.authoring import (
    SkillWriteRequest,
    gated_skill_write,
    resolve_consent_identity,
)
from stackowl.skills.manifest import SkillManifest

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tools.registry import ConsequentialActionGate


_LOOKBACK_DAYS_DEFAULT = 30
_SECONDS_PER_DAY = 86_400
_MIN_EVIDENCE = 3  # mirrors ToolOutcomeMiner._MIN_EVIDENCE

# Consent-policy identities for this module's gated writes (same split as
# skills/synthesizer.py — see resolve_consent_identity's docstring). LIVE is
# used when an interactive turn is in flight; SCHEDULED is the genuinely
# unattended background-job identity an operator seeds with TrustTier.AUTO.
_CONSENT_TOOL_NAME_LIVE = "failure_outcome_miner"
_CONSENT_TOOL_NAME_SCHEDULED = "failure_outcome_miner_scheduled"

CapabilityTagLookup = Callable[[str], "str | None"]


@dataclass(frozen=True)
class RcaVerdict:
    """Contract Task 6/7 must satisfy: one verified root-cause-analysis
    result for a single failure cluster, ready to become a learned SKILL.md.

    This is the interface a future staged-RCA session (Task 6) concludes
    with and a future consumer (Task 7) hands to
    :meth:`FailureOutcomeMiner.mine`. Kept deliberately plain (no coupling to
    Parliament's internal debate/session types) so Task 6/7 can construct it
    from whatever internal shape they use, as long as they map onto these
    fields.

    Attributes:
        capability_class: identifies the cluster this verdict resolves — must
            match a :class:`FailureCluster`'s ``capability_class`` (a tool's
            ``capability_tag``, e.g. ``"web_knowledge"``, or the raw
            ``tool_name`` when no tag is registered for it — see module
            docstring's fallback note).
        failure_class: the other half of the clustering key — must match the
            cluster's ``failure_class`` (``TaskOutcome.failure_class``).
        skill_name: proposed slug for the SKILL.md directory
            (``learned/<skill_name>/``) — must match
            ``SkillManifest.name``'s pattern (``^[a-z][a-z0-9_-]*$``).
        description: short (<300 char) ``SkillManifest.description``.
        when_to_use: short (<300 char) ``SkillManifest.when_to_use``.
        root_cause: human-readable diagnosis of WHY the cluster failed —
            becomes the "Root cause" section of the authored skill body.
        fix_pattern: human-readable reusable fix/mitigation — becomes the
            "Fix / pattern" section of the authored skill body.
        verified: RCA sessions can conclude inconclusively; only
            ``verified=True`` verdicts are ever authored into a skill. An
            unverified verdict is treated exactly like "no verdict yet" —
            the cluster is silently skipped, never partially written.
        confidence: optional [0, 1] confidence score, carried through for a
            future caller's own thresholding — this miner does not itself
            gate on it (that's Task 6/7's call, once they have grounds to).
        parent_trace_ids: trace_ids used as ``SkillManifest.parent_traces``.
            When empty, the miner falls back to the cluster's own outcomes.
    """

    capability_class: str
    failure_class: str
    skill_name: str
    description: str
    when_to_use: str
    root_cause: str
    fix_pattern: str
    verified: bool = True
    confidence: float | None = None
    parent_trace_ids: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_class, self.failure_class)


@dataclass(frozen=True)
class FailureCluster:
    """One ``(capability_class, failure_class)`` bucket of failed outcomes."""

    capability_class: str
    failure_class: str
    outcomes: tuple[TaskOutcome, ...]

    @property
    def size(self) -> int:
        return len(self.outcomes)

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_class, self.failure_class)


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_LEADING_NON_ALPHA_RE = re.compile(r"^[^a-z]+")


def _canonical_incident_slug(capability_class: str, failure_class: str) -> str:
    """Deterministic per-incident identity slug — same formula as
    ``staged_rca._slugify``'s fallback (ponytail: duplicated, not imported —
    that module's private helper is a cross-module boundary this task
    doesn't own; same one-line formula, kept in sync by inspection).

    Used as the skill's on-disk directory name so a still-open incident gets
    exactly ONE authored skill no matter how many mining passes fire for it
    (across scheduler ticks, or across a process restart — see
    ``_author_one``): identity is keyed on the incident's
    ``(capability_class, failure_class)``, never on the LLM's freely-proposed
    ``verdict.skill_name`` — an RCA verifier prompted twice for the same
    incident is not guaranteed to propose the same name text twice.
    """
    raw = f"incident_{capability_class}_{failure_class}"
    slug = _SLUG_RE.sub("_", raw.strip().lower())
    slug = _LEADING_NON_ALPHA_RE.sub("", slug).strip("_-")
    return slug or "incident_fix"


def _capability_class_for(tool: str, tag_lookup: CapabilityTagLookup | None) -> str:
    """Resolve *tool*'s clustering key: its registered ``capability_tag``, or
    the raw tool name when none is registered (documented fallback — a tool
    with no declared siblings is a capability class of one)."""
    if tag_lookup is None:
        return tool
    tag = tag_lookup(tool)
    return tag if tag else tool


def cluster_failures_by_capability_and_signature(
    outcomes: list[TaskOutcome], *, min_size: int = _MIN_EVIDENCE,
    capability_tag_lookup: CapabilityTagLookup | None = None,
) -> list[FailureCluster]:
    """Bucket FAILED outcomes by ``(capability_class, failure_class)``.

    ``capability_class`` resolves via *capability_tag_lookup* (falling back to
    the raw tool name when no tag is registered — see module docstring).

    Root-cause fix (migration 0078): ``failure_class`` is a property of the
    whole TASK, not of each tool call — crediting it to every tool named in
    ``tool_sequence`` blames innocent tools that merely co-occurred with the
    real offender in the same failed turn. When ``o.failed_capability`` is
    known, the outcome is credited ONLY to that single capability. When it is
    ``None`` (historical rows captured before this field existed, or any turn
    where it genuinely couldn't be determined), this falls back to the OLD
    co-occurrence behavior — crediting every distinct capability named in
    ``tool_sequence`` — so old data keeps its (less precise) signal instead of
    dropping to zero. Either way, one outcome contributes at most once per
    ``(capability, failure_class)`` bucket, no matter how many times that
    capability's tool appears in ``tool_sequence``. A bucket only survives if
    it has ``>= min_size`` DISTINCT outcomes — the same evidence-count gate
    the success miner uses (``_MIN_EVIDENCE``), applied to the opposite data.
    """
    buckets: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        if not o.failure_class or not o.tool_sequence:
            continue
        if o.failed_capability:
            capabilities = {_capability_class_for(o.failed_capability, capability_tag_lookup)}
        else:
            capabilities = {
                _capability_class_for(tool, capability_tag_lookup) for tool in o.tool_sequence
            }
        for capability in capabilities:
            buckets[(capability, o.failure_class)].append(o)
    return [
        FailureCluster(capability_class=key[0], failure_class=key[1], outcomes=tuple(members))
        for key, members in buckets.items()
        if len(members) >= min_size
    ]


@dataclass(frozen=True)
class MiningReport:
    """Aggregate counts from one mining run."""

    n_outcomes_scanned: int
    n_clusters_found: int
    n_skills_written: int


class FailureOutcomeMiner:
    """Scan task_outcomes -> cluster failures -> author SKILL.md via the
    shared gate, for clusters with a matching verified :class:`RcaVerdict`."""

    def __init__(
        self,
        outcome_store: TaskOutcomeStore,
        skill_store: SkillIndexStore,
        skills_root: Path,
        *,
        consent_gate: ConsequentialActionGate | None = None,
        lookback_days: int = _LOOKBACK_DAYS_DEFAULT,
        min_evidence: int = _MIN_EVIDENCE,
        capability_tag_lookup: CapabilityTagLookup | None = None,
    ) -> None:
        self._outcomes = outcome_store
        self._skills = skill_store
        self._root = skills_root
        self._consent_gate = consent_gate
        self._lookback_days = lookback_days
        self._min_evidence = min_evidence
        self._capability_tag_lookup = capability_tag_lookup
        log.memory.debug(
            "[incident] miner.init: ready",
            extra={"_fields": {
                "lookback_days": lookback_days,
                "min_evidence": min_evidence,
                "has_capability_lookup": capability_tag_lookup is not None,
            }},
        )

    async def mine(
        self, verdicts: Mapping[tuple[str, str], RcaVerdict],
    ) -> MiningReport:
        """One mining pass: scan outcomes, cluster failures, author a
        SKILL.md (via the shared gate) for every cluster that both meets the
        evidence threshold AND has a matching ``verified=True`` verdict.

        ``verdicts`` is keyed by ``(capability_class, failure_class)`` — the
        same tuple :class:`FailureCluster.key`/:class:`RcaVerdict.key`
        expose. Today this map is hand-built by the caller (Task 6/7 don't
        exist yet); once they do, they populate this same map from a
        completed RCA session before calling ``mine``.
        """
        # 1. ENTRY
        log.memory.info(
            "[incident] miner.mine: entry",
            extra={"_fields": {"n_verdicts": len(verdicts)}},
        )
        since = time.time() - self._lookback_days * _SECONDS_PER_DAY
        try:
            outcomes = await self._outcomes.list_failed_global(since_epoch=since)
        except AttributeError:
            log.memory.warning(
                "[incident] miner.mine: outcome_store has no list_failed_global helper — skip",
            )
            return MiningReport(0, 0, 0)
        # 2. DECISION — cluster, then only act on buckets with a verified verdict.
        clusters = cluster_failures_by_capability_and_signature(
            outcomes, min_size=self._min_evidence,
            capability_tag_lookup=self._capability_tag_lookup,
        )
        log.memory.debug(
            "[incident] miner.mine: clustered",
            extra={"_fields": {"n_outcomes": len(outcomes), "n_clusters": len(clusters)}},
        )
        written = 0
        for cluster in clusters:
            verdict = verdicts.get(cluster.key)
            if verdict is None or not verdict.verified:
                log.memory.debug(
                    "[incident] miner.mine: cluster has no verified verdict — skip",
                    extra={"_fields": {
                        "capability_class": cluster.capability_class,
                        "failure_class": cluster.failure_class,
                        "size": cluster.size,
                    }},
                )
                continue
            if await self._author_one(cluster, verdict):
                written += 1
        # 4. EXIT
        report = MiningReport(
            n_outcomes_scanned=len(outcomes),
            n_clusters_found=len(clusters),
            n_skills_written=written,
        )
        log.memory.info(
            "[incident] miner.mine: exit",
            extra={"_fields": {
                "n_outcomes": report.n_outcomes_scanned,
                "n_clusters": report.n_clusters_found,
                "n_written": report.n_skills_written,
            }},
        )
        return report

    async def _author_one(self, cluster: FailureCluster, verdict: RcaVerdict) -> bool:
        """One gated write: build the manifest/body from *verdict*, then run
        it through the SAME ``security_scan_gate`` -> consent -> write ->
        index chokepoint Task 4 wired for the success-clustering path. Never
        writes directly to disk itself.

        The entire authoring PREP (manifest, body/SKILL.md rendering, consent
        identity, request construction) is one try/except so a bad cluster
        (e.g. a verdict whose text trips a rendering edge case) is logged and
        skipped WITHOUT aborting the rest of the mining pass — mirrors the
        manifest-validation catch this always had, just widened to cover the
        other prep steps too. ``gated_skill_write`` itself never raises (see
        its own docstring), so nothing after this block needs its own catch.
        """
        log.skills.debug(
            "[incident] miner.author_one: entry",
            extra={"_fields": {"skill_name": verdict.skill_name, "cluster_size": cluster.size}},
        )
        # Identity-scoped dedup — a still-open incident (the SAME
        # (capability_class, failure_class)) re-triggers a mining pass on every
        # scheduler tick until IncidentEscalationHandler's dedup closes it, AND
        # that dedup state is in-memory only (reset on every process restart).
        # Without this check, each re-trigger authored ANOTHER near-identical
        # skill because ``verdict.skill_name`` is LLM-proposed free text (not
        # guaranteed identical across RCA runs for the same incident) and the
        # old collision-avoidance loop treated any name clash as "unrelated,
        # pick the next free suffix" — producing e.g. shell_retry_loop_breaker
        # through -13 for ONE recurring incident. Keying the directory name on
        # the incident's OWN (capability_class, failure_class) instead of the
        # proposed name makes a second mining pass for the same incident a
        # real, detectable collision: skip authoring, don't suffix-bump.
        target_dir = self._root / "learned" / _canonical_incident_slug(
            cluster.capability_class, cluster.failure_class,
        )
        if target_dir.exists():
            log.skills.info(
                "[incident] miner.author_one: skill already exists for this "
                "incident — skip (no duplicate authored)",
                extra={"_fields": {
                    "capability_class": cluster.capability_class,
                    "failure_class": cluster.failure_class,
                    "existing_dir": target_dir.name,
                }},
            )
            return False
        final_name = target_dir.name
        try:
            manifest = SkillManifest(
                name=final_name,
                description=verdict.description[:300],
                when_to_use=verdict.when_to_use[:300],
                version="0.1.0",
                source="learned",
                category="incident",
                parent_traces=list(
                    verdict.parent_trace_ids or [o.trace_id for o in cluster.outcomes[:10]]
                ),
            )
            body = _render_incident_body(verdict)
            skill_md_text = _render_skill_md(manifest, body)
            tool_name, channel, session_id = resolve_consent_identity(
                live_tool_name=_CONSENT_TOOL_NAME_LIVE,
                scheduled_tool_name=_CONSENT_TOOL_NAME_SCHEDULED,
            )
            request = SkillWriteRequest(
                target_dir=target_dir, manifest=manifest, body=body,
                skill_md_text=skill_md_text,
                consent_summary=(
                    f"Auto-author incident-fix skill '{final_name}' from a "
                    f"{cluster.size}-failure cluster "
                    f"(capability={cluster.capability_class}, "
                    f"failure_class={cluster.failure_class})"
                ),
                tool_name=tool_name, channel=channel, session_id=session_id,
                category="incident",
            )
        except Exception as exc:  # B5 — never raise out of a mining pass
            log.skills.warning(
                "[incident] miner.author_one: authoring prep failed — skipping",
                exc_info=exc, extra={"_fields": {"skill_name": final_name}},
            )
            return False

        result = await gated_skill_write(
            request, store=self._skills, consent_gate=self._consent_gate,
        )
        if not result.ok:
            log.skills.warning(
                "[incident] miner.author_one: gated write refused — skipping",
                extra={"_fields": {"skill_name": final_name, "reason": result.reason}},
            )
            return False
        log.skills.info(
            "[incident] miner.author_one: exit — written + indexed",
            extra={"_fields": {"skill_name": final_name}},
        )
        return True


def _render_incident_body(verdict: RcaVerdict) -> str:
    """Markdown body for an incident-fix skill: root cause + fix pattern."""
    return (
        f"# Root cause\n\n{verdict.root_cause.strip()}\n\n"
        f"## Fix / pattern\n\n{verdict.fix_pattern.strip()}\n"
    )


def _render_skill_md(manifest: SkillManifest, body: str) -> str:
    """Render SKILL.md text (YAML frontmatter + body).

    ponytail: deliberate small duplicate of synthesizer.py's private
    ``_emit_skill_md`` (same strip-agent-fields-from-frontmatter behavior) —
    that function is private to its own module, so this stays local rather
    than importing a leading-underscore name across a module boundary. If a
    third caller needs this, promote both to a shared public helper.
    """
    fm_dict = manifest.model_dump(mode="json", exclude_none=True)
    for field_name in ("success_rate", "n_executions", "parent_traces", "embedding_model"):
        fm_dict.pop(field_name, None)
    fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False).rstrip("\n")
    return f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"
