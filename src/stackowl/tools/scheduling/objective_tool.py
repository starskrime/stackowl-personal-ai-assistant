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
from stackowl.objectives.model import Objective, SubgoalSpec
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
    repo: str | None = Field(
        default=None,
        description=(
            "Path to a git repo — makes this an EPIC: decomposed into a "
            "dependency graph of stories that run concurrently in isolated "
            "worktrees, verified by tests, and auto-merged into an internal "
            "integration branch. CONSEQUENTIAL: requires consent (stories "
            "run unattended, bypassPermissions). Omit for a plain objective."
        ),
    )


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
            "it), or a clock-driven recurring job (use cronjob). Pass 'repo' to make "
            "this an EPIC — a coding objective decomposed into stories that run "
            "unattended and concurrently in isolated worktrees, auto-merged when "
            "verified; requires the user's explicit consent before it starts."
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
                "repo": {
                    "type": "string",
                    "description": (
                        "Path to a git repo — makes this an EPIC: decomposed into a "
                        "dependency graph of stories that run concurrently in "
                        "isolated worktrees, verified by tests, and auto-merged "
                        "into an internal integration branch. CONSEQUENTIAL: "
                        "requires the user's consent (stories run unattended, "
                        "bypassPermissions). Omit for a plain objective."
                    ),
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

    def consent_summary(self, **call_args: object) -> str | None:
        """Bounded consent digest for an EPIC (repo-bearing) call only —
        mirrors execute_code's per-call summary. A plain objective (no repo)
        has nothing consequential to summarize; returns None (falls back to
        the static description, which is fine since the gate is never
        consulted for a plain call — see execute())."""
        repo = call_args.get("repo")
        if not isinstance(repo, str) or not repo:
            return None
        intent = call_args.get("intent")
        intent = intent if isinstance(intent, str) else ""
        digest = intent[:200] + ("…" if len(intent) > 200 else "")
        return (
            f"Run an EPIC in {repo}: decompose \"{digest}\" into stories that "
            "run UNATTENDED and CONCURRENTLY, each with permission_mode="
            "bypassPermissions (full shell access, isolated to a worktree — "
            "not sandboxed from network/host side effects). Auto-merges "
            "each verified story into an internal integration branch; you "
            "confirm once at the end to merge into your real branch."
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

        repo = args.repo.strip() if args.repo else None
        if repo:
            gated = await self._gate_epic_consent(repo=repo, intent=intent, t0=t0)
            if gated is not None:
                return gated

        objective_id = f"obj-{uuid.uuid4().hex[:8]}"
        base_branch: str | None = None
        integration_branch: str | None = None
        if repo:
            from stackowl.tools.system.git_tool import current_branch

            base_branch = await current_branch(repo)
            if base_branch is None:
                return self._err(f"could not determine the current branch in {repo!r}", t0)
            integration_branch = f"stackowl/epic-{objective_id}"

        objective = Objective(
            objective_id=objective_id,
            owner_id=DEFAULT_PRINCIPAL_ID,
            intent=intent,
            channel=channel_str,
            target_channels=target_channels,
            target_addresses=target_addresses,
            repo=repo,
            integration_branch=integration_branch,
            base_branch=base_branch,
        )
        try:
            if repo:
                from stackowl.tools.system.shell import run_argv

                assert integration_branch is not None  # set above whenever repo is set
                branch_result = await run_argv(
                    ["git", "branch", integration_branch],
                    tool_name="git", workdir=repo, intent="write",
                )
                if not branch_result.success:
                    return self._err(
                        f"could not create integration branch: {branch_result.error}", t0,
                    )

            store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
            await store.create(objective)
            await store.append_event(objective_id, "created", intent)

            if repo:
                decomposer = (
                    ObjectiveDecomposer(services.provider_registry)
                    if services.provider_registry else None
                )
                specs = (
                    await decomposer.decompose_epic_specs(intent)
                    if decomposer else [SubgoalSpec(description=intent)]
                )
                from stackowl.objectives.graph import validate_graph

                graph_error = validate_graph(specs)
                if graph_error is not None:
                    await store.update_status(objective_id, "abandoned")
                    return self._err(
                        f"invalid story dependency graph ({graph_error.kind}): "
                        f"{graph_error.detail}",
                        t0,
                    )
            else:
                decomposer = (
                    ObjectiveDecomposer(services.provider_registry)
                    if services.provider_registry else None
                )
                specs = (
                    await decomposer.decompose_specs(intent)
                    if decomposer else [SubgoalSpec(description=intent)]
                )

            await store.add_subgoals(objective_id, specs)
            await store.append_event(
                objective_id, "decomposed", f"{len(specs)} step(s)"
            )
            # The payload surfaces plain step descriptions (the criteria are an
            # internal acceptance concern, persisted on the sub-goal rows).
            subgoals = [s.description for s in specs]
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

    async def _gate_epic_consent(self, *, repo: str, intent: str, t0: float) -> ToolResult | None:
        """Require consent before creating an EPIC — the ONE consent point for
        its entire unattended run (Consent posture, design spec). Mirrors
        shell.py's `_gate_catastrophic`: `ObjectiveTool.manifest.action_severity`
        stays "write" unconditionally (no per-call manifest variance — the
        tool ABC has no seam for that); this calls the SAME consent policy
        directly instead. Returns a refused ToolResult when consent must NOT
        proceed (no interactive user, no gate wired, declined); returns None
        when approved. Fail-closed on every path, matching every other
        consequential gate in this codebase."""
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")
        if not interactive or not session_id or not channel:
            log.tool.warning(
                "objective.execute: epic creation with no interactive user — refused",
                extra={"_fields": {"repo": repo}},
            )
            return self._err(
                "refused: creating an epic requires an interactive user to "
                "approve unattended execution, and none is present", t0,
            )
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error(
                "objective.execute: epic creation but no consent gate wired — refused",
            )
            return self._err(
                "refused: epic creation requires a consent gate, none is available", t0,
            )
        try:
            allowed = await gate.policy.request(
                tool_name="objective",
                channel=channel,
                session_id=session_id,
                category="epic_execution",
                summary=self.consent_summary(intent=intent, repo=repo) or "",
            )
        except Exception as exc:  # fail-closed on any gate error
            log.tool.error(
                "objective.execute: consent gate raised — refused",
                exc_info=exc,
            )
            return self._err("refused: consent check failed", t0)
        if not allowed:
            log.tool.info("objective.execute: epic creation declined by user")
            return self._err("declined by user", t0)
        return None

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
