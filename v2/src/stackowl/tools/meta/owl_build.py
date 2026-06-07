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
from typing import TYPE_CHECKING

from stackowl.commands.config_helpers import config_path
from stackowl.commands.owls_command import OwlsCommand
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import _SECRETARY_NAME
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.meta.owl_build_authz import build_agent_manifest
from stackowl.tools.meta.owl_build_existence import existing_near_match
from stackowl.tools.meta.owl_build_guards import (
    MAX_AGENT_OWLS,
    consent_summary,
    count_agent_owls,
    name_quality_error,
)
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec, validate_owl_build_spec

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.manifest import SkillSource

# Isolated toolset group so a read-only / non-admin owl never gets owl-administration
# hydrated into its presented toolset.
_TOOLSET_GROUP = "owl_admin"
# A NON-dangerous, tool-declared consent category — the model cannot relax its gating.
_CONSENT_CATEGORY = "owl_build"
# Source name under which agent-minted owls register (so they survive/unregister cleanly).
_SOURCE_NAME = "agent_owls"
# Audit source — reuses the skills audit sink's "learned" lane for provenance (DRY with
# tool_build), so owl create/edit/retire is provenance-tracked the same way.
_AUDIT_SOURCE: SkillSource = "learned"
_ACTOR = "agent_self:owl_build"

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

    async def _create(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Mint a NEW specialist owl. Security order: collision/name-quality (before
        forge) → soft-cap (HARD gate before consent) → existence-redirect → forge →
        consent → persist+register with rollback. Nothing persists before consent."""
        svc = get_services()
        registry = svc.owl_registry
        if registry is None:
            log.tool.error(
                "owl_build.execute: no owl registry wired — cannot create",
                exc_info=None,
                extra={"_fields": {"name": spec.name}},
            )
            return self._err("owl registry unavailable — cannot create an owl.", t0)

        ctx = TraceContext.get()
        creator = str(ctx.get("owl_name") or _SECRETARY_NAME)

        # 1. Collision / reserved — never shadow Secretary or an existing owl.
        if spec.name.strip().lower() == _SECRETARY_NAME or self._exists(registry, spec.name):
            return self._err(
                f"an owl named '{spec.name}' already exists (or is reserved).", t0
            )

        # 2. Name quality (structural, language-neutral) — before any forge.
        nq = name_quality_error(spec.name, registry)
        if nq is not None:
            return self._err(nq, t0)

        # 3. Soft cap — a HARD gate BEFORE consent (the human shouldn't be asked to
        #    approve an owl we'd refuse anyway).
        current = count_agent_owls(registry)
        if current >= MAX_AGENT_OWLS:
            return self._err(
                f"you already have {current} agent-created owls (cap {MAX_AGENT_OWLS}) — "
                "retire one (action='retire') or delegate_task to an existing owl instead.",
                t0,
            )

        # 4. Existence redirect — a near-identical owl is a delegation opportunity.
        match = await existing_near_match(spec, registry, svc)
        if match is not None:
            return self._err(
                f"an existing owl '{match}' already covers this — delegate_task to it "
                "instead of minting a near-duplicate.",
                t0,
            )

        # 5. Forge — authority forced server-side (origin/created_by/creation_ceiling).
        manifest, dropped = build_agent_manifest(
            spec,
            creator=creator,
            parent_ceiling=TraceContext.creation_ceiling(),
            registry=registry,
        )

        # 6. Consent — the real clamp. Surface tools, drops and the existing roster.
        resolved_tools = (
            (manifest.bounds.tools or frozenset()) if manifest.bounds else frozenset()
        )
        summary = consent_summary(
            name=manifest.name,
            role=manifest.role,
            resolved_tools=resolved_tools,
            dropped=dropped,
            roster=tuple(m.name for m in registry.all() if m.origin == "agent"),
            why=spec.specialty or "",
        )
        refusal = await self._consent_or_refuse(summary, manifest.name)
        if refusal is not None:
            return self._err(refusal, t0)

        # 7. Persist with rollback. Snapshot the yaml first so a failed register can
        #    restore the exact prior bytes (10k-DB-safe: never leave a half state).
        snapshot = self._yaml_snapshot()
        try:
            OwlsCommand()._upsert_to_yaml(manifest_to_yaml_entry(manifest))  # noqa: SLF001
        except Exception as exc:  # B5 — no-hidden-errors
            log.tool.error(
                "owl_build.execute: persist failed — nothing registered",
                exc_info=exc,
                extra={"_fields": {"owl": manifest.name}},
            )
            self._yaml_restore(snapshot)
            return self._err(f"failed to persist owl '{manifest.name}': {exc}", t0)

        await self._audit("create", manifest.name, creator)

        # 8. Register LIVE — on failure restore the yaml snapshot (atomic rollback).
        try:
            registry.register(manifest, source_name=_SOURCE_NAME)
        except Exception as exc:  # B5 — roll back the persisted yaml
            log.tool.error(
                "owl_build.execute: live registration failed — rolling back yaml",
                exc_info=exc,
                extra={"_fields": {"owl": manifest.name}},
            )
            self._yaml_restore(snapshot)
            await self._audit("delete", manifest.name, creator)
            return self._err(
                f"failed to register owl '{manifest.name}' ({exc}) — rolled back.", t0
            )

        # 9. Success.
        tools_str = ", ".join(sorted(resolved_tools)) or "(none)"
        msg = (
            f"Created owl '{manifest.name}' ({manifest.role}). Tools: {tools_str}."
        )
        if dropped:
            msg += f" Dropped above your authority: {', '.join(sorted(dropped))}."
        msg += " Delegate to it with delegate_task."
        return self._ok(msg, t0, extra={"owl": manifest.name, "op": "create"})

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _exists(registry: object, name: str) -> bool:
        """True if an owl named ``name`` is already registered (case-sensitive get)."""
        getter = getattr(registry, "get", None)
        if getter is None:
            return False
        try:
            getter(name)
            return True
        except Exception:  # OwlNotFoundError — the not-found path is expected
            return False

    @staticmethod
    def _yaml_snapshot() -> bytes | None:
        """Read the owls yaml file's raw bytes (the same file ``_upsert_to_yaml``
        writes), or None if it is absent. Logs + returns None on read error."""
        path = config_path()
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            log.tool.error(
                "owl_build.execute: yaml snapshot read failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            return None

    @staticmethod
    def _yaml_restore(snapshot: bytes | None) -> None:
        """Restore the owls yaml to ``snapshot`` (or unlink if it had not existed)."""
        path = config_path()
        try:
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(snapshot)
        except OSError as exc:
            log.tool.error(
                "owl_build.execute: yaml rollback failed — manual cleanup may be needed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )

    async def _audit(self, op: str, name: str, actor: str) -> None:
        """Append a provenance audit row via the skills audit sink (best-effort).

        Mirrors :meth:`tool_build._audit` (source='learned'); a missing store
        degrades to a log line — the yaml persist already succeeded. Never raises."""
        store = get_services().skill_store
        if store is None:
            log.tool.info(
                "owl_build.execute: no skill store — audit skipped (owl still persisted)",
                extra={"_fields": {"owl": name, "op": op}},
            )
            return
        try:
            await store.audit_write(
                skill_name=name,
                source=_AUDIT_SOURCE,
                op=op,
                actor=actor,
                details={"kind": "agent_owl", "created_by": actor},
            )
        except Exception as exc:  # B5 — never fail the build on an audit hiccup
            log.tool.warning(
                "owl_build.execute: audit_write failed — owl persisted, audit pending",
                exc_info=exc,
                extra={"_fields": {"owl": name, "op": op}},
            )

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
