"""Shared gated skill-authoring write path.

Single chokepoint for ANY agent-driven write of a ``learned`` SKILL.md —
whether triggered by a live user turn or an unattended scheduled job — so
every write goes through the SAME sequence ``skill_manage.py`` enforces for
human-initiated authoring:

    security_scan_gate (HARD, fails closed on scanner crash)
    -> consent (ConsentPolicy.request, fails closed off-TTY / unconfigured
       trust tier)
    -> write SKILL.md to disk
    -> store.upsert (index the write)

Before this module existed, :class:`~stackowl.skills.synthesizer.SkillSynthesizer`
(the daily success-clustering job) wrote directly via
``target_dir.mkdir()``/``write_text()`` + ``store.upsert()`` with NEITHER gate
consulted — a live safety hole: a poisoned/dangerous skill body authored by
the LLM-as-author prompt would land on disk and get indexed with no scan and
no consent check at all, then re-inject as trusted first-party context on the
next turn. This module closes that bypass and is the ONE place the write path
lives, so a future incident-clustering miner reuses the same gate instead of
re-implementing it.

Scheduled callers have no live user turn / ``TraceContext`` in flight, so
there is no consequential-action DISPATCH WRAPPER to intercept them the way
the pipeline intercepts a human-invoked ``skill_manage`` tool call (that gate
runs via ``ConsequentialActionGate.check()`` in the tool registry, driven by
the tool's ``action_severity="consequential"`` manifest field, BEFORE
``execute()`` is ever reached). :func:`gated_skill_write` therefore calls the
consent gate EXPLICITLY — mirroring ``tool_build.py``'s own
``_consent_or_refuse`` self-authorization pattern — rather than relying on
being invoked through that dispatch wrapper.

NOTE ON SCHEDULED-JOB CONSENT IDENTITY (flagged, deliberately NOT resolved
here): a background job has no live user to approve a prompt. With today's
default ``ConsentPolicy`` (empty trust tiers, off-TTY/unregistered-channel
prompters deny), gating the synthesizer this way means its writes are denied
by default until an operator explicitly configures a trust tier (e.g.
``TrustTier.AUTO``) for the ``tool_name`` a caller passes in. That is a
deliberate security-over-availability trade-off, not a bug — closing the
silent-write bypass matters more than preserving "always writes" for an
unattended job. Deciding HOW unattended skill-authoring should eventually get
approved (a digest-and-approve flow, a scoped trust tier, etc.) is a
product/security decision for a later task, not this one.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.skills.loader import LoadedSkill
from stackowl.tools.knowledge.skill_validation import security_scan_gate

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.skills.manifest import SkillManifest
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tools.registry import ConsequentialActionGate

_SKILL_MD = "SKILL.md"


def live_or_scheduled_identity(
    *, fallback_channel: str = "scheduler", fallback_session: str = "scheduler",
) -> tuple[str, str]:
    """Consent-policy channel/session identity for a gated write.

    Skill-authoring can be reached two ways: an unattended scheduled job (the
    daily ``SkillSynthesizer`` pass — no live turn, no ``TraceContext``) OR a
    live user turn (e.g. the ``synthesize_skills`` tool, which is ALREADY
    dispatched through ``ConsequentialActionGate.check()`` because its own
    manifest declares ``action_severity="consequential"`` — so a human already
    approved "run synthesis now" before ``execute()`` ran at all).

    When a live, interactive turn IS in flight, use ITS channel/session_id so
    the SAME per-channel prompter (Telegram/Slack/CLI) that could gate the
    outer tool call can also gate this inner write — a real human can approve
    it live, rather than an identity ("scheduler") no prompter is ever
    registered for. Absent that (the genuinely unattended scheduled job), fall
    back to a stable background-job identity that fails closed by design
    until an operator configures a trust tier for it.
    """
    ctx = TraceContext.get()
    channel = ctx.get("channel")
    session_id = ctx.get("session_id")
    if ctx.get("interactive") and channel and session_id:
        return str(channel), str(session_id)
    return fallback_channel, fallback_session


@dataclass(frozen=True)
class SkillWriteRequest:
    """Everything needed to gate, write, and index ONE SKILL.md.

    ``skill_md_text`` is the fully-rendered file content (frontmatter + body,
    e.g. via ``_emit_skill_md``); ``body`` is just the markdown body, used to
    build the :class:`LoadedSkill` handed to ``store.upsert``. ``tool_name``
    is the consent-policy identity presented to :class:`ConsentPolicy` — give
    the success-clustering synthesizer and a future incident miner DIFFERENT
    names so an operator can configure trust tiers independently per caller.
    """

    target_dir: Path
    manifest: SkillManifest
    body: str
    skill_md_text: str
    consent_summary: str
    tool_name: str
    channel: str = "scheduler"
    session_id: str = "scheduler"
    category: str | None = None
    tools_registered: int = 0
    owls_registered: int = 0


@dataclass(frozen=True)
class SkillWriteResult:
    """Outcome of a gated write attempt.

    ``ok=False`` ⇒ nothing was written to disk and ``store.upsert`` was never
    called — the caller must treat this exactly like any other "skip this
    cluster/skill" outcome (no partial state).
    """

    ok: bool
    reason: str = ""
    loaded: LoadedSkill | None = None


async def gated_skill_write(
    request: SkillWriteRequest,
    *,
    store: SkillIndexStore,
    consent_gate: ConsequentialActionGate | None,
) -> SkillWriteResult:
    """``security_scan_gate`` -> consent -> write SKILL.md -> ``store.upsert``.

    Never raises: any unexpected exception is logged and treated as a BLOCK
    (fail closed), matching the rest of the skill-authoring surface.
    """
    name = request.manifest.name
    # 1. ENTRY
    log.skills.debug(
        "[authoring] gated_skill_write: entry",
        extra={"_fields": {
            "name": name, "target_dir": str(request.target_dir),
            "tool_name": request.tool_name,
        }},
    )
    try:
        # 2. DECISION — HARD security gate first; a dangerous body never
        # reaches the consent prompt (and never touches the real tree).
        blocked = _scan_or_block(request.skill_md_text, name)
        if blocked is not None:
            log.skills.warning(
                "[authoring] gated_skill_write: security gate BLOCKED — nothing written",
                extra={"_fields": {"name": name}},
            )
            return SkillWriteResult(ok=False, reason=blocked)

        allowed, deny_reason = await _consent_or_refuse(request, consent_gate)
        if not allowed:
            log.skills.warning(
                "[authoring] gated_skill_write: consent DENIED — nothing written",
                extra={"_fields": {"name": name, "tool_name": request.tool_name}},
            )
            return SkillWriteResult(ok=False, reason=deny_reason)

        # 3. STEP — both gates passed: perform the real write + index it.
        request.target_dir.mkdir(parents=True, exist_ok=True)
        (request.target_dir / _SKILL_MD).write_text(request.skill_md_text, encoding="utf-8")

        loaded = LoadedSkill(
            manifest=request.manifest, path=request.target_dir, body=request.body,
            tools_registered=request.tools_registered,
            owls_registered=request.owls_registered,
        )
        await store.upsert(loaded)
    except Exception as exc:  # B5 — never raise out of the write path
        log.skills.error(
            "[authoring] gated_skill_write: unexpected failure — failing closed",
            exc_info=exc, extra={"_fields": {"name": name}},
        )
        return SkillWriteResult(ok=False, reason=f"internal error: {type(exc).__name__}: {exc}")

    # 4. EXIT
    log.skills.info(
        "[authoring] gated_skill_write: exit — written + indexed",
        extra={"_fields": {"name": name, "target_dir": str(request.target_dir)}},
    )
    return SkillWriteResult(ok=True, loaded=loaded)


def _scan_or_block(skill_md_text: str, name: str) -> str | None:
    """Stage *skill_md_text* in a sibling temp dir and run ``security_scan_gate``.

    Mirrors ``skill_manage.py``'s ``_scan_or_block``: the scanner sees the
    exact would-be tree WITHOUT ever touching the real skill directory.
    """
    staging_parent = tempfile.mkdtemp(prefix="stackowl-skillscan-")
    try:
        staged = Path(staging_parent) / name
        staged.mkdir(parents=True, exist_ok=True)
        (staged / _SKILL_MD).write_text(skill_md_text, encoding="utf-8")
        ok, reason = security_scan_gate(staged)
        if not ok:
            return f"BLOCKED by security scan — no change made.\n{reason}"
        return None
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


async def _consent_or_refuse(
    request: SkillWriteRequest, consent_gate: ConsequentialActionGate | None,
) -> tuple[bool, str]:
    """Consult the consent policy directly. No gate wired ⇒ fail closed.

    Unlike a live tool call (dispatched through
    ``ConsequentialActionGate.check()`` ahead of ``Tool.execute()``), a
    scheduled skill-authoring pass has no ambient Tool/pipeline dispatch to
    catch it — so this calls ``consent_gate.policy.request(...)`` explicitly,
    the same self-authorization pattern ``tool_build.py`` uses for its own
    consequential action.
    """
    if consent_gate is None:
        log.skills.error(
            "[authoring] consent: no consent gate wired — refusing (fail closed)",
            extra={"_fields": {"tool_name": request.tool_name}},
        )
        return False, "refused: no consent gate available to approve this skill write."
    try:
        allowed = await consent_gate.policy.request(
            tool_name=request.tool_name,
            channel=request.channel,
            session_id=request.session_id,
            category=request.category,
            summary=request.consent_summary,
        )
    except Exception as exc:  # no-hidden-errors — fail closed
        log.skills.error(
            "[authoring] consent: gate raised — refusing (fail closed)",
            exc_info=exc, extra={"_fields": {"tool_name": request.tool_name}},
        )
        return False, f"refused: consent check failed ({type(exc).__name__})."
    if not allowed:
        return False, "declined — consent policy denied this skill write."
    return True, ""
