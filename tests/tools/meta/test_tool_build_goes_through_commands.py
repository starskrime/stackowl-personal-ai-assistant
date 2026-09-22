"""Story 4.9, AC1 — ``tool_build``'s create/delete each submit their
declared ``owls.build_tool.*`` command instead of writing the spec file +
registering the tool directly.

Drives the GENUINE :class:`ToolBuildTool` against a real db + registry
(mirrors ``test_tool_build_reversible_consent.py``'s own harness shape),
spies on ``submit_command`` to prove exactly one declared command type is
submitted per action, and confirms the real effect landed AND that create's
own undo (``owls.build_tool.delete``) actually removes it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta import tool_build as tool_build_mod
from stackowl.tools.meta.tool_build import ToolBuildTool
from stackowl.tools.meta.tool_build_commands import CREATE, DELETE
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


def _services(tmp_db: DbPool) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(ConsentPolicy(tiers={"tool_build": TrustTier.AUTO})),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


def _spy_submit_command(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = tool_build_mod.submit_command

    async def _spy(db, command_type, payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command_type)
        return await real(db, command_type, payload, **kwargs)

    monkeypatch.setattr(tool_build_mod, "submit_command", _spy)
    return calls


def _create_args(name: str = "shout") -> dict:
    return {
        "action": "create",
        "name": name,
        "description": "echo a string verbatim via printf",
        "params": [{"name": "text", "type": "string", "description": "the text", "required": True}],
        "argv_template": ["printf", "%s", "{text}"],
        "action_severity": "read",
    }


def test_create_and_delete_never_call_registry_write_or_unlink_directly() -> None:
    """Structural proof (mirrors owl_build's own precedent): neither method
    body writes the spec file or touches the registry — only submit_command."""
    create_src = inspect.getsource(ToolBuildTool._create)
    assert "submit_command" in create_src
    for forbidden in ("registry.register(", "spec_path.write_text("):
        assert forbidden not in create_src

    delete_src = inspect.getsource(ToolBuildTool._delete)
    assert "submit_command" in delete_src
    for forbidden in ("registry.unregister(", "spec_path.unlink("):
        assert forbidden not in delete_src


@pytest.mark.asyncio
async def test_create_submits_owls_build_tool_create(
    tmp_home: Path, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(tmp_db)
    svc_token = set_services(services)
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli", owl_name="secretary",
    )
    calls = _spy_submit_command(monkeypatch)
    try:
        result = await ToolBuildTool().execute(**_create_args())
    finally:
        TraceContext.reset(trace)
        reset_services(svc_token)

    assert result.success, result.error
    assert calls == [CREATE]
    assert (StackowlHome.learned_tools_dir() / "shout.json").exists()
    assert services.tool_registry.get("shout") is not None


@pytest.mark.asyncio
async def test_delete_submits_owls_build_tool_delete_and_undo_recreates(
    tmp_home: Path, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.commands.spec.undo import request_undo

    services = _services(tmp_db)
    svc_token = set_services(services)
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli", owl_name="secretary",
    )
    try:
        created = await ToolBuildTool().execute(**_create_args(name="gone_soon"))
        assert created.success, created.error

        calls = _spy_submit_command(monkeypatch)
        result = await ToolBuildTool().execute(action="delete", name="gone_soon")
        assert result.success, result.error
        assert calls == [DELETE]
        assert not (StackowlHome.learned_tools_dir() / "gone_soon.json").exists()
        assert services.tool_registry.get("gone_soon") is None

        # Boundaries — delete's undo IS create (captured spec content).
        from stackowl.commands.spec.registry import CommandSpecRegistry

        # Locate the command_id submit_command minted for the delete.
        rows = await tmp_db.fetch_all(
            "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
            (DELETE,),
        )
        assert CommandSpecRegistry.get(DELETE).undo_command_type == CREATE
        undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
        assert undo_outcome.refusal is None, undo_outcome.refusal
        assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
        assert undo_outcome.submission.outcome.success
        assert (StackowlHome.learned_tools_dir() / "gone_soon.json").exists()
    finally:
        TraceContext.reset(trace)
        reset_services(svc_token)


@pytest.mark.asyncio
async def test_undo_a_create_deletes_the_new_tool(
    tmp_home: Path, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding, 2026-09-22 pass: owls.build_tool.create never captured
    an undo_payload, so request_undo's fallback resubmitted the CREATE
    command's own LearnedToolSpec payload straight into DeleteToolPayload's
    extra="forbid" model and raised an uncaught ValidationError. Now fixed:
    create's own handler captures {name} explicitly."""
    from stackowl.commands.spec.undo import request_undo

    services = _services(tmp_db)
    svc_token = set_services(services)
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli", owl_name="secretary",
    )
    try:
        created = await ToolBuildTool().execute(**_create_args(name="undo_me"))
        assert created.success, created.error

        rows = await tmp_db.fetch_all(
            "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
            (CREATE,),
        )
        undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
        assert undo_outcome.refusal is None, undo_outcome.refusal
        assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
        assert undo_outcome.submission.outcome.success
        assert not (StackowlHome.learned_tools_dir() / "undo_me.json").exists()
        assert services.tool_registry.get("undo_me") is None
    finally:
        TraceContext.reset(trace)
        reset_services(svc_token)
