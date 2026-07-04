"""Task 8 — tool_build routes its consent through the ONE ConsequentialActionGate
with ``reversible=True`` (its undo handle is ``action='delete'``).

A reversible, non-always-ask create AUTO-PROCEEDS: the prompt path is skipped (the
user is never bothered) but the AUDIT path is still exercised (the decision is
recorded with reason ``reversible_auto``). An always-ask category still prompts —
the fail-closed default is preserved wherever the reversible relaxation does not
genuinely apply.

Drives the GENUINE :class:`ToolBuildTool` against a REAL registry + consent policy;
only the consent PROMPTER is a spy (so we can assert it was/wasn't invoked) and the
AI provider is absent (tool_build never consults it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, ConsentScope
from stackowl.tools.meta.tool_build import ToolBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _SpyPrompter:
    """Records every prompt so a test can assert it was (or was NOT) consulted."""

    def __init__(self, scope: ConsentScope = ConsentScope.DENY) -> None:
        self.calls = 0
        self._scope = scope

    async def prompt(self, req: object) -> ConsentScope:
        self.calls += 1
        return self._scope


class _AuditSpy:
    """AuditLogger-shaped sink (``.append``) capturing each finalized decision."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, event: str, *, actor: str, target: str, details: dict) -> None:
        self.rows.append(details)


def _create_args(name: str = "shout") -> dict:
    return {
        "action": "create",
        "name": name,
        "description": "echo a string verbatim via printf",
        "params": [{"name": "text", "type": "string", "description": "the text", "required": True}],
        "argv_template": ["printf", "%s", "{text}"],
        "action_severity": "read",
    }


def _services(tmp_db: DbPool, gate: ConsequentialActionGate) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


async def _run(services: StepServices, args: dict) -> object:
    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_id="s-tb", trace_id="t-tb", interactive=True, channel="cli",
        owl_name="secretary",
    )
    try:
        return await ToolBuildTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)


async def test_reversible_create_auto_proceeds_without_prompt(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """A reversible tool_build create skips the PROMPT but exercises the AUDIT."""
    spy = _SpyPrompter()
    audit = _AuditSpy()
    # Default tiers (ALWAYS_ASK) + default always-ask lists (which do NOT contain
    # tool_build) — the ONLY reason it auto-proceeds is the reversible relaxation.
    gate = ConsequentialActionGate(
        ConsentPolicy(prompter=spy, audit_logger=audit)  # type: ignore[arg-type]
    )
    services = _services(tmp_db, gate)

    result = await _run(services, _create_args())

    assert result.success, result.error
    # The user was NEVER prompted (auto-proceed) …
    assert spy.calls == 0, "reversible create must not bother the user with a prompt"
    # … but the decision WAS audited as a reversible auto-allow.
    assert any(d.get("reason") == "reversible_auto" for d in audit.rows), audit.rows
    # OUTCOME — persisted + registered live (it really was built).
    assert (StackowlHome.learned_tools_dir() / "shout.json").exists()
    assert services.tool_registry.get("shout") is not None


async def test_always_ask_tool_still_prompts_despite_reversible(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """With tool_build on the always-ask exclusion list the reversible relaxation is
    NOT applied — it still prompts, and a DENY blocks the build (fail-closed)."""
    spy = _SpyPrompter(ConsentScope.DENY)
    gate = ConsequentialActionGate(
        ConsentPolicy(
            prompter=spy,  # type: ignore[arg-type]
            always_ask_tools=frozenset({"tool_build"}),
        )
    )
    services = _services(tmp_db, gate)

    result = await _run(services, _create_args(name="asked"))

    assert not result.success
    assert spy.calls == 1, "an always-ask tool must still be prompted"
    # OUTCOME — nothing persisted / registered on a denial.
    assert not (StackowlHome.learned_tools_dir() / "asked.json").exists()
    assert services.tool_registry.get("asked") is None
