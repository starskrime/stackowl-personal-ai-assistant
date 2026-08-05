"""StagedRcaSession — fixed-sequential root-cause analysis (NOT open debate).

Why not a Parliament debate
---------------------------
Open-ended multi-agent debate measurably DEGRADES correctness on
factual/diagnostic tasks (the "Deliberative Illusion" finding): owls converge on
the most *confident* peer, not the most *correct* one. Root-cause analysis is
exactly that kind of diagnostic task, so this module deliberately does NOT reuse
:class:`~stackowl.parliament.orchestrator.ParliamentOrchestrator` (parallel
fan-out → convergence loop → synthesis). Instead it runs THREE fixed sequential
stages, each checked against the SAME concrete evidence, never against a peer's
confidence or agreement:

1. **evidence-gatherer** owl — organizes the deterministically-gathered raw
   evidence (health snapshot, failed ``TaskOutcome`` rows, recovery footprint)
   into a clear, structured brief. It does not invent evidence: the raw facts
   are handed to it in the prompt by the caller (the incident handler), gathered
   from real inspectable rows — the owl only structures them.
2. **hypothesis** owl — proposes a root cause + reusable fix/fallback, CITING
   the stage-1 brief.
3. **verifier** owl — checks the hypothesis strictly against the SAME evidence
   brief. It answers ``VERDICT: VERIFIED`` only if the evidence supports the
   hypothesis, ``VERDICT: REJECTED`` otherwise. A rejected (or unparseable)
   verdict yields ``verified=False`` — treated downstream exactly like "no
   verdict yet" (the cluster is never authored into a skill).

What machinery is reused
------------------------
The owl-role primitive: :class:`~stackowl.pipeline.backends.base.OrchestratorBackend`
(``run(state) -> state``) — the SAME single-owl invocation
:meth:`~stackowl.parliament.round_runner.RoundRunner._run_owl` is built on, with
the SAME :class:`~stackowl.pipeline.state.PipelineState` construction shape
(``channel="rca"``, ``interactive=False``, a fresh trace per stage). What is
NOT reused is the debate LOOP (rounds/convergence/parallel fan-out/synthesis) —
that shape is the thing the Deliberative-Illusion finding warns against for
diagnosis. Reusing the backend primitive but driving it as fixed stages is the
whole point.

Output shape
------------
Concludes with an :class:`~stackowl.learning.failure_outcome_miner.RcaVerdict`
(or ``None`` when a stage produced nothing usable). Task 7 consumes the verdict;
this module stops at "here is a verified (or rejected) RCA verdict".
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.learning.failure_outcome_miner import RcaVerdict
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState

# The three fixed stages. Order is load-bearing — evidence THEN hypothesis THEN
# verify — and each owl name doubles as the ``owl_name`` the backend routes on.
_GATHERER = "rca_gatherer"  # OwlAgentManifest.name caps at 16 chars
_HYPOTHESIS = "hypothesis"
_VERIFIER = "verifier"

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_LEADING_NON_ALPHA_RE = re.compile(r"^[^a-z]+")


@dataclass(frozen=True)
class RcaEvidence:
    """Immutable, deterministically-gathered evidence for ONE incident.

    Built by the incident handler from REAL inspectable data (health statuses,
    failed ``TaskOutcome`` rows, recovery/bridging footprint) — never by an LLM.
    Every stage reasons over THIS object's ``brief``; the verifier checks the
    hypothesis against it, so grounding is in concrete rows, not owl confidence.
    """

    incident_id: str
    capability_class: str
    failure_class: str
    brief: str
    parent_trace_ids: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_class, self.failure_class)


@dataclass(frozen=True)
class RcaOwls:
    """Which owl each stage runs as. Defaults are role names; an operator may
    map real personas here without touching the staging logic."""

    gatherer: str = _GATHERER
    hypothesis: str = _HYPOTHESIS
    verifier: str = _VERIFIER


@dataclass
class _StageTrace:
    """Recorded per-stage output (for logging/inspection, not for the verdict)."""

    owl: str
    prompt: str
    output: str
    duration_ms: float


def _block_field(text: str, key: str) -> str | None:
    """Extract a ``KEY: value`` block from structured owl output.

    Value runs from after ``KEY:`` to the next ``ALLCAPS_KEY:`` line or EOF, so a
    multi-line root cause survives. Case-insensitive on the key. Returns None
    when the key is absent (an owl that omitted a field), the empty-string guard
    is the caller's.

    TOLERATES MARKDOWN DECORATION. Measured 2026-08-05 over 15 days of production
    logs: 410 RCAs ended with "missing root_cause/fix — no verdict", and in
    **409 of them BOTH fields were missing** — not one field omitted, the whole
    structure unreadable. The old pattern required the key at column zero,
    bare: ``^ROOT_CAUSE:``. Against eight realistic model outputs it accepted two.
    It rejected ``**ROOT_CAUSE:**`` — the single most common way a model emits a
    labelled field — and also list items, headings, and merely INDENTED keys.

    That last one was an internal inconsistency, not just strictness: the
    terminating lookahead already allowed leading whitespace (``^\\s*[A-Z]``)
    while the match did not, so an indented key was invisible as a field and yet
    still ended the previous one.

    This is a PARSER fix, not a prompt fix, on purpose. Making the model comply is
    a per-model negotiation that a weaker or swapped backend re-opens; making the
    reader tolerant is done once. No English is matched — only structural
    decoration (list bullets, heading hashes, emphasis marks).
    """
    # Optional leading decoration: indentation, a list bullet or number, a
    # heading marker, and emphasis marks. Deliberately all optional, so the
    # previously-accepted bare form still parses identically.
    decoration = r"[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)?(?:#{1,6}[ \t]*)?[*_`]{0,3}[ \t]*"
    # A following key terminates the value — matched with the SAME decoration, or
    # a decorated key would fail to end the previous field and swallow the rest.
    terminator = rf"^{decoration}[A-Z][A-Z_]+[*_`]{{0,3}}[ \t]*:"
    pattern = re.compile(
        rf"^{decoration}{re.escape(key)}[*_`]{{0,3}}[ \t]*:[ \t]*[*_`]{{0,3}}[ \t]*"
        rf"(.*?)(?={terminator}|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return None
    val = _strip_decoration(m.group(1))
    return val or None


#: Trailing artifacts left by a model that wrapped its answer in a code fence or
#: closed its own emphasis. Stripped from the END of a captured value only.
_TRAILING_NOISE_RE = re.compile(r"(?:\s|`{3,}|[*_]{1,3})+$")


def _strip_decoration(value: str) -> str:
    """Trim whitespace plus dangling fence/emphasis marks from a captured value."""
    return _TRAILING_NOISE_RE.sub("", value.strip()).strip()


def _slugify(raw: str, fallback: str) -> str:
    """Coerce a proposed skill name to ``^[a-z][a-z0-9_-]*$`` (SkillManifest.name).

    The hypothesis owl's suggestion is never trusted to match the pattern, so it
    is always sanitized; an empty/invalid result falls back to ``fallback``
    (which the caller derives deterministically from the incident key)."""
    slug = _SLUG_RE.sub("_", raw.strip().lower())
    slug = _LEADING_NON_ALPHA_RE.sub("", slug).strip("_-")
    return slug or fallback


#: Per-stage wall-clock budget. Sized from MEASUREMENT, not taste (DEBT-18).
#:
#: The previous 30s was calibrated for a fast model. On the deployment that
#: surfaced this, a single model call had a p90 of 93s — and a stage is a whole
#: agentic turn that may make several. The evidence was that the successful-RCA
#: duration distribution was TRUNCATED exactly at the old limit (max 28.5s per
#: stage against a 30s timeout), which is the signature of a budget cutting into
#: real work rather than bounding a hang: 323 timeouts against 605 verdicts, and
#: 100% failure on the last day observed.
#:
#: Generous but finite, following the same treatment the numeric-limits arc gave
#: GOVERNOR_ACQUIRE_TIMEOUT_SECONDS (45s -> 1800s): removing the bound outright
#: would let one stuck stage sit forever. The ceiling that makes this safe is the
#: scheduler's own _HANDLER_TIMEOUT_SEC (1200s) — worst case here is three stages
#: plus one retry (4 x 240 = 960s), leaving 240s of margin, and a test pins that
#: relationship so the inner budget can never be silently pre-empted by the outer
#: one. That test earned its place immediately: the first value tried here was
#: 300s, which makes the worst case exactly 1200s — equal to the ceiling rather
#: than under it, so a stage retry could have been killed mid-flight.
DEFAULT_PER_STAGE_TIMEOUT_S = 240.0


class StagedRcaSession:
    """Runs the three fixed RCA stages for ONE incident and returns a verdict.

    Reuses the ``OrchestratorBackend`` owl-role primitive (NOT the Parliament
    debate loop). Each stage is one bounded ``backend.run`` on a fresh
    ``PipelineState``; the stages run STRICTLY sequentially and each later
    stage's prompt embeds the earlier stages' outputs, so the verifier judges
    the hypothesis against the SAME evidence — never a peer's confidence.
    """

    def __init__(
        self,
        backend: OrchestratorBackend,
        *,
        owls: RcaOwls | None = None,
        per_stage_timeout_s: float = DEFAULT_PER_STAGE_TIMEOUT_S,
    ) -> None:
        self._backend = backend
        self._owls = owls or RcaOwls()
        self._per_stage_timeout_s = per_stage_timeout_s

    async def analyze(self, evidence: RcaEvidence) -> RcaVerdict | None:
        """Run evidence-gatherer → hypothesis → verifier for one incident.

        Returns the concluding ``RcaVerdict`` (``verified`` reflects the verifier
        stage) or ``None`` when a stage produced nothing usable (hard failure —
        distinct from a clean REJECTED verdict, which still returns an object so
        the rejection is inspectable/loggable).
        """
        # 1. ENTRY
        log.parliament.info(
            "[rca] staged.analyze: entry",
            extra={"_fields": {
                "incident_id": evidence.incident_id,
                "capability_class": evidence.capability_class,
                "failure_class": evidence.failure_class,
            }},
        )
        t0 = time.monotonic()
        traces: list[_StageTrace] = []
        try:
            # STAGE 1 — organize the raw evidence into a structured brief.
            brief = await self._run_stage(
                evidence, self._owls.gatherer,
                _gatherer_prompt(evidence), traces,
            )
            if not brief.strip():
                log.parliament.warning(
                    "[rca] staged.analyze: evidence stage empty — no verdict",
                    extra={"_fields": {"incident_id": evidence.incident_id}},
                )
                return None

            # STAGE 2 — propose a root cause + fix, citing the stage-1 brief.
            hypothesis = await self._run_stage(
                evidence, self._owls.hypothesis,
                _hypothesis_prompt(evidence, brief), traces,
            )
            if not hypothesis.strip():
                log.parliament.warning(
                    "[rca] staged.analyze: hypothesis stage empty — no verdict",
                    extra={"_fields": {"incident_id": evidence.incident_id}},
                )
                return None

            # STAGE 3 — VERIFY the hypothesis against the SAME evidence brief.
            verdict_text = await self._run_stage(
                evidence, self._owls.verifier,
                _verifier_prompt(evidence, brief, hypothesis), traces,
            )
        except Exception as exc:  # a stage backend failure must not raise into the sweep
            log.parliament.error(
                "[rca] staged.analyze: a stage failed — no verdict",
                exc_info=exc,
                extra={"_fields": {"incident_id": evidence.incident_id}},
            )
            return None

        verdict = _build_verdict(evidence, hypothesis, verdict_text)
        # 4. EXIT
        log.parliament.info(
            "[rca] staged.analyze: exit",
            extra={"_fields": {
                "incident_id": evidence.incident_id,
                "verified": verdict.verified if verdict else None,
                "skill_name": verdict.skill_name if verdict else None,
                "duration_ms": (time.monotonic() - t0) * 1000.0,
                "stages": [t.owl for t in traces],
            }},
        )
        return verdict

    async def _run_stage(
        self,
        evidence: RcaEvidence,
        owl_name: str,
        prompt: str,
        traces: list[_StageTrace],
    ) -> str:
        """One owl invocation via the backend primitive (mirrors RoundRunner._run_owl).

        Builds a fresh internal ``PipelineState`` — ``channel="rca"``,
        ``interactive=False`` (no user is answering mid-analysis), a unique
        trace — runs it bounded by ``per_stage_timeout_s``, and returns the
        concatenated response text. 3. STEP logging per the 4-point standard."""
        log.parliament.debug(
            "[rca] staged._run_stage: dispatch",
            extra={"_fields": {
                "incident_id": evidence.incident_id, "owl": owl_name,
                "prompt_len": len(prompt),
            }},
        )
        state = PipelineState(
            trace_id=str(uuid.uuid4()),
            # The incident is this analysis run's lane — RCA is not a user chat.
            session_key=evidence.incident_id,
            input_text=prompt,
            channel="rca",
            owl_name=owl_name,
            pipeline_step="rca_stage",
            interactive=False,
            # This stage has no user stream and no reply_target — the text is read
            # straight off final.responses below, not delivered. Without this the
            # deliver step's stream-miss fallback fires on every stage (no target),
            # logging a loud "answer not delivered" warning for output that was
            # never meant to be delivered in the first place.
            defer_delivery=True,
        )
        t0 = time.monotonic()
        # DEBT-18 — a timed-out stage RETRIES ONCE before the incident is lost.
        # Previously any timeout propagated straight out of analyze(), which
        # discarded the whole run including stages that had already succeeded,
        # and nothing ever re-ran it: that incident simply never got a verdict.
        # Bounded at one retry on purpose — every stage is a paid model turn, so
        # a genuinely stuck stage must stop rather than spin. The happy path is
        # untouched (no extra call when the first attempt returns).
        final = None
        for attempt in (1, 2):
            try:
                final = await asyncio.wait_for(
                    self._backend.run(state), timeout=self._per_stage_timeout_s,
                )
                break
            except TimeoutError:
                if attempt == 1:
                    log.parliament.warning(
                        "[rca] staged._run_stage: stage timed out — retrying once",
                        extra={"_fields": {
                            "incident_id": evidence.incident_id, "owl": owl_name,
                            "timeout_s": self._per_stage_timeout_s,
                        }},
                    )
                    continue
                log.parliament.error(
                    "[rca] staged._run_stage: stage timed out twice — giving up",
                    extra={"_fields": {
                        "incident_id": evidence.incident_id, "owl": owl_name,
                        "timeout_s": self._per_stage_timeout_s, "attempts": 2,
                    }},
                )
                raise
        assert final is not None  # noqa: S101 — the loop either breaks or raises
        text = "".join(c.content for c in final.responses)
        traces.append(_StageTrace(
            owl=owl_name, prompt=prompt, output=text,
            duration_ms=(time.monotonic() - t0) * 1000.0,
        ))
        log.parliament.debug(
            "[rca] staged._run_stage: result",
            extra={"_fields": {
                "incident_id": evidence.incident_id, "owl": owl_name,
                "output_len": len(text),
            }},
        )
        return text


# ---------------------------------------------------------------------------
# Prompt builders — each later stage embeds the earlier outputs so the verifier
# judges against the SAME evidence, never a peer's confidence.
# ---------------------------------------------------------------------------

def _gatherer_prompt(evidence: RcaEvidence) -> str:
    return (
        "You are the EVIDENCE-GATHERER in a fixed-stage incident root-cause "
        "analysis. You are NOT debating anyone. Organize the raw evidence below "
        "into a clear, factual brief. Do not speculate about the cause yet — "
        "only structure what the evidence actually shows.\n\n"
        f"Incident: capability_class={evidence.capability_class}, "
        f"failure_class={evidence.failure_class}\n\n"
        f"RAW EVIDENCE:\n{evidence.brief}\n\n"
        "Respond with a concise EVIDENCE brief (bullet points of concrete facts)."
    )


def _hypothesis_prompt(evidence: RcaEvidence, brief: str) -> str:
    return (
        "You are the HYPOTHESIS owl in a fixed-stage incident root-cause "
        "analysis. Using ONLY the evidence brief below, propose the single most "
        "likely root cause and a reusable fix or fallback. Cite the specific "
        "evidence that supports each claim.\n\n"
        f"Incident: capability_class={evidence.capability_class}, "
        f"failure_class={evidence.failure_class}\n\n"
        f"EVIDENCE BRIEF:\n{brief}\n\n"
        "Respond EXACTLY in this format:\n"
        "SKILL_NAME: <short_snake_case_slug>\n"
        "DESCRIPTION: <one sentence, <300 chars>\n"
        "WHEN_TO_USE: <one sentence, <300 chars>\n"
        "ROOT_CAUSE: <why this cluster failed, citing the evidence>\n"
        "FIX: <reusable fix or fallback pattern>"
    )


def _verifier_prompt(evidence: RcaEvidence, brief: str, hypothesis: str) -> str:
    return (
        "You are the VERIFIER owl in a fixed-stage incident root-cause analysis. "
        "Your ONLY job is to check whether the hypothesis is supported by the "
        "EVIDENCE BRIEF — not by how confident the hypothesis sounds, not by any "
        "agreement. If the evidence does NOT concretely support the root cause, "
        "REJECT it.\n\n"
        f"EVIDENCE BRIEF:\n{brief}\n\n"
        f"HYPOTHESIS:\n{hypothesis}\n\n"
        "Respond EXACTLY in this format:\n"
        "VERDICT: VERIFIED   (only if the evidence supports it) or REJECTED\n"
        "CONFIDENCE: <0.0-1.0>\n"
        "ROOT_CAUSE: <the evidence-supported root cause, refined if needed>\n"
        "FIX: <the evidence-supported fix/fallback, refined if needed>"
    )


def _build_verdict(
    evidence: RcaEvidence, hypothesis: str, verdict_text: str,
) -> RcaVerdict | None:
    """Assemble the final ``RcaVerdict`` from the hypothesis + verifier outputs.

    ``verified`` is True IFF the verifier emitted ``VERDICT: VERIFIED``; a
    REJECTED or missing verdict yields ``verified=False`` (the miner treats that
    exactly like "no verdict"). Root-cause/fix prefer the verifier's refined
    text, falling back to the hypothesis's. Returns None only when there is no
    usable root-cause text at all (nothing to author)."""
    verdict_token = (_block_field(verdict_text, "VERDICT") or "").strip().upper()
    verified = verdict_token.startswith("VERIFIED")

    root_cause = (
        _block_field(verdict_text, "ROOT_CAUSE")
        or _block_field(hypothesis, "ROOT_CAUSE")
    )
    fix_pattern = (
        _block_field(verdict_text, "FIX")
        or _block_field(hypothesis, "FIX")
    )
    if not root_cause or not fix_pattern:
        log.parliament.warning(
            "[rca] build_verdict: missing root_cause/fix — no verdict",
            extra={"_fields": {
                "incident_id": evidence.incident_id,
                "has_root_cause": bool(root_cause),
                "has_fix": bool(fix_pattern),
            }},
        )
        return None

    fallback_slug = _slugify(
        f"incident_{evidence.capability_class}_{evidence.failure_class}", "incident_fix",
    )
    proposed = _block_field(hypothesis, "SKILL_NAME") or ""
    skill_name = _slugify(proposed, fallback_slug)

    description = (
        _block_field(hypothesis, "DESCRIPTION")
        or f"Learned fix for recurring {evidence.failure_class} in "
        f"{evidence.capability_class}."
    )[:300]
    when_to_use = (
        _block_field(hypothesis, "WHEN_TO_USE")
        or f"When {evidence.capability_class} fails with {evidence.failure_class}."
    )[:300]

    confidence: float | None = None
    conf_raw = _block_field(verdict_text, "CONFIDENCE")
    if conf_raw:
        m = re.search(r"[0-9]*\.?[0-9]+", conf_raw)
        if m:
            try:
                confidence = max(0.0, min(1.0, float(m.group(0))))
            except ValueError:
                confidence = None

    return RcaVerdict(
        capability_class=evidence.capability_class,
        failure_class=evidence.failure_class,
        skill_name=skill_name,
        description=description,
        when_to_use=when_to_use,
        root_cause=root_cause,
        fix_pattern=fix_pattern,
        verified=verified,
        confidence=confidence,
        parent_trace_ids=evidence.parent_trace_ids,
    )


def fallback_verdict(
    evidence: RcaEvidence, *, reason: str,
) -> RcaVerdict:
    """A deterministic 'alternative needed' verdict for a KNOWN-non-retryable
    failure class — produced WITHOUT running the 3-stage RCA.

    A structurally non-retryable failure (a deterministic domain/config error:
    the capability fundamentally cannot do this, retry is doomed) does not merit
    an RCA cycle — the fix is always "use an alternative capability". This is the
    AWS-Bedrock-retry-guidance short-circuit: don't diagnose what a retry could
    never fix; route straight to substitution. ``verified=True`` because the
    recommendation (substitute) is itself certain, not a diagnosis needing
    evidence."""
    slug = _slugify(
        f"substitute_{evidence.capability_class}_{evidence.failure_class}",
        "substitute_capability",
    )
    return RcaVerdict(
        capability_class=evidence.capability_class,
        failure_class=evidence.failure_class,
        skill_name=slug,
        description=(
            f"{evidence.capability_class} fails deterministically with "
            f"{evidence.failure_class} — use an alternative capability."
        )[:300],
        when_to_use=(
            f"When {evidence.capability_class} raises {evidence.failure_class}."
        )[:300],
        root_cause=(
            f"Non-retryable failure: {reason}. Retrying or recycling "
            f"{evidence.capability_class} cannot resolve a deterministic "
            f"{evidence.failure_class}."
        ),
        fix_pattern=(
            "Do not retry the same capability. Substitute an alternative "
            "capability for this class of request (capability substitution)."
        ),
        verified=True,
        confidence=1.0,
        parent_trace_ids=evidence.parent_trace_ids,
    )
