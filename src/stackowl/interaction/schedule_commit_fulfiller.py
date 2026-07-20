"""ScheduleCommitFulfiller — turn a text-only scheduling promise into a REAL job.

Overclaim trigger 4 (``ScheduleCommitClassifier``) detects a draft that promises
future scheduled work with no ``schedules``-effect tool call behind it. The
original remedy replaced the whole draft with an honest "I didn't actually
schedule anything — want me to?" floor. Honest, but it pushes the work back to
the user: the platform KNEW what was promised and could have just done it.

This fulfiller is the do-the-action upgrade: extract ``(goal, schedule)`` from
the draft with one bounded fast-tier call, then create the job through the real
:class:`CronjobTool` — the exact same code path the model SHOULD have called,
with all of its protections intact (prompt security scan, per-owl soft cap,
schedule-grammar validation, ownership tagging). On success the user keeps the
original answer plus a truthful "scheduled" receipt; the promise is now backed
by a verified job row, so there is nothing left to confess. On ANY failure —
no provider, unparseable extraction, invalid schedule, cronjob refusal — the
caller falls back to the existing honest floor, unchanged.

Fail-safe direction: ``fulfill`` returns ``None`` on every degraded path and
never raises. A ``None`` costs the user one clarifying question (the old
behavior); a wrong non-None would mint a job the user didn't ask for — so the
extraction is strict (exact schedule grammar, one-word NONE escape hatch,
validated by the same ``is_valid_schedule`` the tool itself uses) and the job
itself is created only through CronjobTool's own guarded create path.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from stackowl.infra.observability import log, traced_span
from stackowl.providers.base import Message, ModelProvider
from stackowl.tools.scheduling.cron_helpers import is_valid_schedule

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

# Caps mirror ScheduleCommitClassifier's hygiene: bounded prompt, bounded logs.
_MAX_TEXT_CHARS = 600
_LOG_TEXT_CHARS = 80
_MAX_TOKENS = 120

_SYSTEM_PROMPT = (
    "The ASSISTANT's reply promised to do something for the user LATER (ping, "
    "remind, check, monitor, report back). Extract the promise as JSON so it "
    "can actually be scheduled:\n"
    '{"goal": "<imperative description of the future task, in the '
    'conversation\'s language>", "schedule": "<when>"}\n'
    "\"schedule\" MUST use exactly one of these forms:\n"
    "  'in 5m' / 'in 2h'  — one-time, relative delay\n"
    "  'at 17:00'         — one-time, next occurrence of that local clock time\n"
    "  'every 30m' / 'every 2h' — recurring interval\n"
    "  'daily@09:00'      — recurring, daily at that local time\n"
    "Pick the form matching the promise. If the reply names NO inferable time "
    "at all, respond with the single word NONE. Respond with ONLY the JSON "
    "object (or NONE) — no prose, no code fences."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class ScheduleCommitFulfiller:
    """Extract a promised (goal, schedule) and mint the job via CronjobTool."""

    def __init__(self, provider_registry: ProviderRegistry, *, timeout_s: float = 15.0) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def fulfill(self, *, response: str, request: str) -> str | None:
        """Create the promised job; return a truthful receipt line, or ``None``.

        ``None`` on every degraded path (caller falls back to the honest
        ask-floor). Never raises.
        """
        # 1. ENTRY
        log.engine.debug(
            "schedule_commit_fulfiller.fulfill: entry",
            extra={"_fields": {"response_len": len(response), "request_len": len(request)}},
        )
        extracted = await self._extract(response, request)
        if extracted is None:
            return None
        goal, schedule = extracted

        # 3. STEP — mint through the REAL tool (security scan, caps, ownership,
        # grammar re-validation all live there; imported lazily to keep this
        # module import-light on the interaction package).
        from stackowl.tools.scheduling.cronjob import CronjobTool

        try:
            result = await CronjobTool().execute(action="create", prompt=goal, schedule=schedule)
        except Exception as exc:  # B5 — the tool never raises, but belt-and-suspenders
            log.engine.error(
                "schedule_commit_fulfiller.fulfill: cronjob create raised — fallback to floor",
                exc_info=exc,
                extra={"_fields": {"schedule": schedule}},
            )
            return None
        if not result.success:
            log.engine.warning(
                "schedule_commit_fulfiller.fulfill: cronjob create refused — fallback to floor",
                extra={"_fields": {
                    "schedule": schedule,
                    "error": (result.error or "")[:_LOG_TEXT_CHARS],
                }},
            )
            return None

        # 4. EXIT — the promise is now BACKED by a real job row; the receipt is
        # truthful by construction (rendered only after a successful create).
        log.engine.info(
            "schedule_commit_fulfiller.fulfill: promise fulfilled — job created",
            extra={"_fields": {"goal": goal[:_LOG_TEXT_CHARS], "schedule": schedule}},
        )
        return f"\n\n📅 Scheduled: {goal} ({schedule})"

    # ------------------------------------------------------------------ helpers

    async def _extract(self, response: str, request: str) -> tuple[str, str] | None:
        """One bounded fast-tier call → validated ``(goal, schedule)`` or ``None``."""
        provider = self._resolve_provider()
        if provider is None:
            log.engine.warning(
                "schedule_commit_fulfiller._extract: no fast provider — fallback to floor",
            )
            return None
        user_text = "\n".join(
            [
                f"USER ASKED: {request[:_MAX_TEXT_CHARS]}",
                f"ASSISTANT REPLIED: {response[:_MAX_TEXT_CHARS]}",
            ]
        )
        try:
            async with traced_span(
                log.engine, "schedule_commit_fulfiller._extract.provider_call"
            ):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model="",
                        max_tokens=_MAX_TOKENS,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
        except TimeoutError:
            log.engine.warning(
                "schedule_commit_fulfiller._extract: provider timed out — fallback to floor",
                extra={"_fields": {"timeout_s": self._timeout_s}},
            )
            return None
        except Exception as exc:
            log.engine.error(
                "schedule_commit_fulfiller._extract: provider call failed — fallback to floor",
                exc_info=exc,
            )
            return None
        return self._parse(result.content or "")

    def _resolve_provider(self) -> ModelProvider | None:
        try:
            return self._registry.get_by_tier("fast")
        except Exception as exc:
            log.engine.warning(
                "schedule_commit_fulfiller._resolve_provider: get_by_tier failed",
                exc_info=exc,
            )
            return None

    @staticmethod
    def _parse(raw: str) -> tuple[str, str] | None:
        """Parse the model's JSON (or NONE) into a validated (goal, schedule)."""
        text = raw.strip()
        if not text or text.upper().startswith("NONE"):
            log.engine.info(
                "schedule_commit_fulfiller._parse: no inferable time — fallback to floor",
            )
            return None
        match = _JSON_RE.search(text)
        if match is None:
            log.engine.warning(
                "schedule_commit_fulfiller._parse: no JSON in extraction — fallback to floor",
                extra={"_fields": {"raw": text[:_LOG_TEXT_CHARS]}},
            )
            return None
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            log.engine.warning(
                "schedule_commit_fulfiller._parse: invalid JSON — fallback to floor",
                extra={"_fields": {"raw": text[:_LOG_TEXT_CHARS]}},
            )
            return None
        goal = str(payload.get("goal", "")).strip()
        schedule = str(payload.get("schedule", "")).strip()
        # Same validator CronjobTool's create path uses — reject before minting.
        if not goal or not schedule or not is_valid_schedule(schedule):
            log.engine.warning(
                "schedule_commit_fulfiller._parse: empty goal or invalid schedule — fallback to floor",
                extra={"_fields": {"schedule": schedule[:_LOG_TEXT_CHARS], "has_goal": bool(goal)}},
            )
            return None
        return goal, schedule
