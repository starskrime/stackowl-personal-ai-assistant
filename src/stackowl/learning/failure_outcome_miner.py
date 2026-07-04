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

Naming note (verified against the real schema, not assumed): the plan brief
describes bucketing by ``(capability_class, error_signature)``.
``TaskOutcome`` (:mod:`stackowl.memory.outcome_store`) has neither field name.
The closest existing analogues are:

* ``capability_class`` -> per-tool name, extracted from ``tool_sequence`` —
  exactly what :class:`~stackowl.learning.tool_outcome_miner.ToolOutcomeMiner`
  already buckets by for its ``tool_name`` key.
* ``error_signature``  -> ``failure_class`` (the exception class name derived
  by ``classify_failure`` in ``outcome_store.py``).

No new fields are invented on ``TaskOutcome`` — this module reuses the two
fields that already carry that meaning.

RCA integration point (for Task 6: incident trigger + staged RCA, and Task 7:
consume RCA result): clustering + threshold logic lives here, standalone of
any RCA machinery. A cluster only becomes a SKILL.md when the caller supplies
a matching, ``verified=True`` :class:`RcaVerdict` for its
``(tool_name, failure_class)`` key. Task 6/7 will eventually produce these
from a completed RCA session; for THIS task they are hand-built (see tests).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
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
        tool_name: identifies the cluster this verdict resolves — must match
            a :class:`FailureCluster`'s ``tool_name`` (the "capability" half
            of the clustering key).
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

    tool_name: str
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
        return (self.tool_name, self.failure_class)


@dataclass(frozen=True)
class FailureCluster:
    """One ``(tool_name, failure_class)`` bucket of failed outcomes."""

    tool_name: str
    failure_class: str
    outcomes: tuple[TaskOutcome, ...]

    @property
    def size(self) -> int:
        return len(self.outcomes)

    @property
    def key(self) -> tuple[str, str]:
        return (self.tool_name, self.failure_class)


def cluster_failures_by_capability_and_signature(
    outcomes: list[TaskOutcome], *, min_size: int = _MIN_EVIDENCE,
) -> list[FailureCluster]:
    """Bucket FAILED outcomes by ``(tool_name, failure_class)``.

    Mirrors :class:`~stackowl.learning.tool_outcome_miner.ToolOutcomeMiner`'s
    per-tool bucketing shape (one credit per tool named in
    ``tool_sequence``), but for the failure side: only outcomes with a
    non-``None`` ``failure_class`` are considered at all, and a bucket only
    survives if it has ``>= min_size`` members — the same evidence-count gate
    the success miner uses (``_MIN_EVIDENCE``), applied to the opposite data.
    """
    buckets: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
    for o in outcomes:
        if not o.failure_class or not o.tool_sequence:
            continue
        for tool in o.tool_sequence:
            buckets[(tool, o.failure_class)].append(o)
    return [
        FailureCluster(tool_name=key[0], failure_class=key[1], outcomes=tuple(members))
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
    ) -> None:
        self._outcomes = outcome_store
        self._skills = skill_store
        self._root = skills_root
        self._consent_gate = consent_gate
        self._lookback_days = lookback_days
        self._min_evidence = min_evidence
        log.memory.debug(
            "[incident] miner.init: ready",
            extra={"_fields": {
                "lookback_days": lookback_days,
                "min_evidence": min_evidence,
            }},
        )

    async def mine(
        self, verdicts: Mapping[tuple[str, str], RcaVerdict],
    ) -> MiningReport:
        """One mining pass: scan outcomes, cluster failures, author a
        SKILL.md (via the shared gate) for every cluster that both meets the
        evidence threshold AND has a matching ``verified=True`` verdict.

        ``verdicts`` is keyed by ``(tool_name, failure_class)`` — the same
        tuple :class:`FailureCluster.key`/:class:`RcaVerdict.key` expose.
        Today this map is hand-built by the caller (Task 6/7 don't exist
        yet); once they do, they populate this same map from a completed RCA
        session before calling ``mine``.
        """
        # 1. ENTRY
        log.memory.info(
            "[incident] miner.mine: entry",
            extra={"_fields": {"n_verdicts": len(verdicts)}},
        )
        since = time.time() - self._lookback_days * _SECONDS_PER_DAY
        try:
            outcomes = await self._outcomes.list_scored_for_owl_global(since_epoch=since)
        except AttributeError:
            log.memory.warning(
                "[incident] miner.mine: outcome_store has no global helper — skip",
            )
            return MiningReport(0, 0, 0)
        # 2. DECISION — cluster, then only act on buckets with a verified verdict.
        clusters = cluster_failures_by_capability_and_signature(
            outcomes, min_size=self._min_evidence,
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
                        "tool_name": cluster.tool_name,
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
        writes directly to disk itself."""
        log.skills.debug(
            "[incident] miner.author_one: entry",
            extra={"_fields": {"skill_name": verdict.skill_name, "cluster_size": cluster.size}},
        )
        target_dir = self._root / "learned" / verdict.skill_name
        try:
            manifest = SkillManifest(
                name=verdict.skill_name,
                description=verdict.description[:300],
                when_to_use=verdict.when_to_use[:300],
                version="0.1.0",
                source="learned",
                category="incident",
                parent_traces=list(
                    verdict.parent_trace_ids or [o.trace_id for o in cluster.outcomes[:10]]
                ),
            )
        except Exception as exc:  # B5 — never raise out of a mining pass
            log.skills.warning(
                "[incident] miner.author_one: SkillManifest validation failed — skipping",
                exc_info=exc, extra={"_fields": {"skill_name": verdict.skill_name}},
            )
            return False

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
                f"Auto-author incident-fix skill '{verdict.skill_name}' from a "
                f"{cluster.size}-failure cluster "
                f"(tool={cluster.tool_name}, failure_class={cluster.failure_class})"
            ),
            tool_name=tool_name, channel=channel, session_id=session_id,
            category="incident",
        )
        result = await gated_skill_write(
            request, store=self._skills, consent_gate=self._consent_gate,
        )
        if not result.ok:
            log.skills.warning(
                "[incident] miner.author_one: gated write refused — skipping",
                extra={"_fields": {"skill_name": verdict.skill_name, "reason": result.reason}},
            )
            return False
        log.skills.info(
            "[incident] miner.author_one: exit — written + indexed",
            extra={"_fields": {"skill_name": verdict.skill_name}},
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
