"""owl_build — the self-extending owl-builder (Phase-2 A).

Mirrors :mod:`stackowl.tools.meta.tool_build` 1:1: a consequential, isolated meta-tool
the agent uses to mint/edit/retire a SPECIALIST OWL — a standing, named, reusable
persona. Like tool_build it is consent-gated (fail-closed off-TTY), validated through a
structured chokepoint, and depth-0 only (defense in depth — the execute step also
child-excludes it so a sub-agent can never recurse into owl creation).

The agent-facing :class:`OwlBuildSpec` carries NO authority fields; origin / created_by /
creation_ceiling / bounds are forced server-side in the action handlers. ``create`` /
``edit`` / ``retire`` land in Tasks 9/10 — they raise ``NotImplementedError`` here.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec, validate_owl_build_spec

# Isolated toolset group so a read-only / non-admin owl never gets owl-administration
# hydrated into its presented toolset.
_TOOLSET_GROUP = "owl_admin"
# A NON-dangerous, tool-declared consent category — the model cannot relax its gating.
_CONSENT_CATEGORY = "owl_build"
# Source name under which agent-minted owls register (so they survive/unregister cleanly).
_SOURCE_NAME = "agent_owls"

_VALID_ACTIONS: tuple[str, ...] = ("create", "edit", "retire")


class OwlBuildTool(Tool):
    """Create / edit / retire a specialist owl (consent-gated, depth-0 only)."""

    @property
    def name(self) -> str:
        return "owl_build"

    @property
    def description(self) -> str:
        return (
            "RARE. Create/edit/retire a SPECIALIST OWL — a standing, named, reusable "
            "persona. Almost every request is NOT this: first answer directly; if it "
            "needs a specialist, delegate_task to an EXISTING owl; only mint a new owl "
            "for a recurring role the human will reuse. Doing a research task once is "
            "NOT a reason to mint a research owl — do the task. action='create' mints a "
            "new owl, 'edit' adjusts one, 'retire' removes one. Requires human approval "
            "(consequential) and fails closed with no interactive user present."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "create | edit | retire",
                },
                "name": {
                    "type": "string",
                    "description": "The owl's name. Required for every action.",
                },
                "preset": {
                    "type": "string",
                    "description": (
                        "A named capability preset for the owl (mutually exclusive with "
                        "explicit_tools). Use for create/edit."
                    ),
                },
                "explicit_tools": {
                    "type": "array",
                    "description": (
                        "An explicit list of tool names the owl may use (mutually "
                        "exclusive with preset). Use for create/edit."
                    ),
                    "items": {"type": "string"},
                },
                "specialty": {
                    "type": "string",
                    "description": (
                        "One sentence describing the owl's standing role. Required for create."
                    ),
                },
                "model_tier": {
                    "type": "string",
                    "description": "Optional model tier hint for the owl.",
                },
            },
            "required": ["action", "name"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            toolset_group=_TOOLSET_GROUP,
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "owl_build.execute: entry",
            extra={"_fields": {"action": kwargs.get("action"), "name": kwargs.get("name")}},
        )
        # 2. Parse + pydantic-validate the agent-facing spec (structured error, no raise).
        try:
            spec = OwlBuildSpec.model_validate(kwargs)
        except Exception as exc:  # B5 — structured block, never a raise
            log.tool.error(
                "owl_build.execute: malformed spec",
                exc_info=exc,
                extra={"_fields": {"args": list(kwargs.keys())}},
            )
            return self._err(f"invalid owl_build request: {exc}", t0)

        # 3. Structured spec validation (preset XOR explicit_tools, specialty, etc.).
        spec_err = validate_owl_build_spec(spec)
        if spec_err is not None:
            return self._err(spec_err, t0)

        # 4. Depth-0 only — also child-excluded at the execute-step dispatch; this is
        # defense in depth so a sub-agent can never recurse into owl creation.
        ctx = TraceContext.get()
        depth = int(ctx.get("delegation_depth", 0) or 0)
        if depth > 0:
            log.tool.error(
                "owl_build.execute: refused at depth>0",
                exc_info=None,
                extra={"_fields": {"depth": depth, "name": spec.name}},
            )
            return self._err(
                "owl_build is only available to the root owl (refused for sub-agents).", t0
            )

        # 5. DECISION — dispatch by validated action (handlers land in Tasks 9/10).
        try:
            if spec.action == "create":
                return await self._create(spec, t0)
            if spec.action == "edit":
                return await self._edit(spec, t0)
            return await self._retire(spec, t0)
        except NotImplementedError:
            return self._err(f"action '{spec.action}' is not yet implemented.", t0)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "owl_build.execute: unhandled failure",
                exc_info=exc,
                extra={"_fields": {"action": spec.action, "name": spec.name}},
            )
            return self._err(f"owl_build failed: {exc}", t0)

    # ------------------------------------------------------------------ consent

    async def _consent_or_refuse(self, summary: str, name: str) -> str | None:
        """Consequential consent, fail-closed off-TTY. Returns a refusal or None."""
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")
        if not interactive or not channel or not session_id:
            log.tool.error(
                "owl_build.execute: no user present to approve — refused (fail closed)",
                exc_info=None,
                extra={"_fields": {"owl": name, "interactive": interactive}},
            )
            return (
                f"refused: building owl '{name}' needs your approval and no "
                "interactive user is present (fail closed)."
            )
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error(
                "owl_build.execute: no consent gate wired — refused (fail closed)",
                exc_info=None,
                extra={"_fields": {"owl": name}},
            )
            return f"refused: no consent gate available to approve building owl '{name}'."
        try:
            allowed = await gate.policy.request(
                tool_name=self.name,
                channel=channel,
                session_id=session_id,
                category=_CONSENT_CATEGORY,
                summary=summary,
            )
        except Exception as exc:  # no-hidden-errors — fail closed
            log.tool.error(
                "owl_build.execute: consent gate raised — refused (fail closed)",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
            return f"refused: consent check failed while building owl '{name}'."
        if not allowed:
            return f"declined by user — owl '{name}' was not built."
        return None

    # ------------------------------------------------------------------ actions

    async def _create(self, spec: OwlBuildSpec, t0: float) -> ToolResult:  # noqa: ARG002
        raise NotImplementedError

    async def _edit(self, spec: OwlBuildSpec, t0: float) -> ToolResult:  # noqa: ARG002
        raise NotImplementedError

    async def _retire(self, spec: OwlBuildSpec, t0: float) -> ToolResult:  # noqa: ARG002
        raise NotImplementedError

    # ------------------------------------------------------------------ results

    @staticmethod
    def _ok(output: str, t0: float, *, extra: dict[str, object] | None = None) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_build.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_build.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
