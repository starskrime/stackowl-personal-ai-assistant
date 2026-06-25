"""ObjectiveTool — the agent-callable producer for standing objectives (1D).

The assistant calls this when the user asks it to hold a STANDING OBJECTIVE it
should work autonomously across many turns until done — "keep an eye on X and
handle it", "work on Y until it's finished". Distinct from ``cronjob`` (a
recurring clock-driven goal) and from a one-off task (just do it now).

On create it: mints an objective, captures the durable delivery target (so the
driver can report back even from a session-less tick), decomposes the intent
EAGERLY into ordered sub-goals (so the user immediately sees the plan), and
persists objective + sub-goals + an activity log. The ``objective_driver``
scheduler handler then advances it. Severity ``write``; group ``scheduling``.
"""

from __future__ import annotations

import json
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.notifications.recipient import resolve_owner_addresses
from stackowl.objectives.decomposer import ObjectiveDecomposer
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.services import get_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.scheduling.cron_security import scan_cron_prompt

_TOOLSET_GROUP = "scheduling"


class ObjectiveArgs(BaseModel):
    """Validated arguments for one ``objective`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(..., description="The standing objective to pursue.")


class ObjectiveTool(Tool):
    """Create a standing objective the assistant works autonomously to completion."""

    @property
    def name(self) -> str:
        return "objective"

    @property
    def description(self) -> str:
        return (
            "Create a STANDING OBJECTIVE the assistant works on its own across many "
            "turns until it is done — e.g. 'keep an eye on X and handle it', 'work "
            "on Y until finished'. It is decomposed into ordered steps and advanced "
            "automatically in the background; you are pinged on completion or if it "
            "hits an irreversible decision only you can make. LANE: durable, "
            "multi-step goals the user wants pursued to completion without "
            "re-asking. ANTI-LANE: a one-off task you can finish right now (just do "
            "it), or a clock-driven recurring job (use cronjob)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "The standing objective to pursue to completion.",
                },
            },
            "required": ["intent"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
            toolset_group=_TOOLSET_GROUP,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        try:
            args = ObjectiveArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError:
            return self._err("invalid arguments — 'intent' is required", t0)
        intent = args.intent.strip()
        log.tool.info(
            "objective.execute: entry",
            extra={"_fields": {"has_intent": bool(intent)}},
        )
        if not intent:
            return self._err("create requires a non-empty 'intent'", t0)

        # Reuse the cron prompt safety gate — the intent is persisted and later
        # rendered/driven, so it gets the same injection/exfil scan.
        ok, reason = scan_cron_prompt(intent)
        if not ok:
            log.tool.warning(
                "objective.execute: intent blocked", extra={"_fields": {"reason": reason}}
            )
            return self._err(f"blocked: {reason}", t0)

        services = get_services()
        db = services.db_pool
        if db is None:
            return self._err("objectives unavailable (no database configured)", t0)

        ctx = TraceContext.get()
        channel = ctx.get("channel")
        channel_str = channel if isinstance(channel, str) else None
        target_channels, target_addresses = self._resolve_durable_target(channel_str)

        objective_id = f"obj-{uuid.uuid4().hex[:8]}"
        objective = Objective(
            objective_id=objective_id,
            owner_id=DEFAULT_PRINCIPAL_ID,
            intent=intent,
            channel=channel_str,
            target_channels=target_channels,
            target_addresses=target_addresses,
        )
        try:
            store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
            await store.create(objective)
            await store.append_event(objective_id, "created", intent)

            decomposer = ObjectiveDecomposer(services.provider_registry) if services.provider_registry else None
            subgoals = await decomposer.decompose(intent) if decomposer else [intent]
            await store.add_subgoals(objective_id, subgoals)
            await store.append_event(
                objective_id, "decomposed", f"{len(subgoals)} step(s)"
            )
        except Exception as exc:  # B5 — never raise out of a tool
            log.tool.error(
                "objective.execute: persist failed — degrading",
                exc_info=exc,
                extra={"_fields": {"objective_id": objective_id}},
            )
            return self._err("could not create the objective (a storage error occurred)", t0)

        payload: dict[str, object] = {
            "created": True,
            "objective_id": objective_id,
            "subgoals": subgoals,
            "step_count": len(subgoals),
        }
        if not target_channels:
            payload["created_but_unreachable"] = True
            payload["warning"] = (
                "Objective created — but progress can't be auto-delivered on this "
                "channel. Use /owls objectives to check on it, or start it from a "
                "chat channel (e.g. Telegram) to receive updates."
            )
        return self._ok(payload, t0)

    # ---------------------------------------------------------------- helpers

    def _resolve_durable_target(
        self, channel: str | None
    ) -> tuple[list[str], dict[str, str | int]]:
        """Resolve the objective's durable ``(target_channels, target_addresses)``.

        Same precedence as the cronjob producer: the live request's reply_target
        first, then the shared owner fallback, else empty (caller signals
        unreachable). Reuses :func:`resolve_owner_addresses` so every producer
        shares one owner→native-target resolver.
        """
        ctx = TraceContext.get()
        reply_target = ctx.get("reply_target")
        if reply_target is not None and channel:
            return [channel], {channel: reply_target}
        settings = get_services().settings
        if settings is not None and channel:
            addresses = resolve_owner_addresses(settings, [channel])
            if addresses:
                return [channel], dict(addresses)
        return [], {}

    @staticmethod
    def _ok(payload: dict[str, object], t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "objective.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=json.dumps(payload), duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "objective.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        # A pre-persist refusal commits no side effect — keep the give-up floor clean.
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms,
            side_effect_committed=False,
        )
