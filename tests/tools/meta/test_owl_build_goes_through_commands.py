"""Story 4.9, AC1 — ``owl_build``'s create/edit/rename/pause/resume/retire
each submit their declared ``owls.build.*``/``scheduling.*_owl_job`` command
instead of calling ``persist_owl``/``registry.replace``/``delete_owl``/
``JobScheduler.pause`` directly.

Drives the GENUINE :class:`OwlBuildTool` against a real registry + db (mirrors
``test_owl_build_ts9_confirm.py``'s own harness shape), spies on
``submit_command`` to prove EXACTLY one declared command type is submitted
per action, and confirms the real effect landed (the command actually ran,
not just that it was asked for).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta import owl_build as owl_build_mod
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.meta.owl_build_commands import CREATE, EDIT, RENAME, RETIRE
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


def _services(tmp_db: DbPool, registry: OwlRegistry) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})
        ),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


def _trace() -> object:
    return TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )


def _spy_submit_command(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wraps the REAL ``submit_command`` (never fakes its behavior) so the
    real effect still lands — only records which command_type each call
    used, proving the tool submits through the one door rather than
    mutating directly."""
    calls: list[str] = []
    real = owl_build_mod.submit_command

    async def _spy(db, command_type, payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command_type)
        return await real(db, command_type, payload, **kwargs)

    monkeypatch.setattr(owl_build_mod, "submit_command", _spy)
    return calls


class TestStructuralNoDirectMutatorCalls:
    """Source-level proof (mirrors the codebase's own
    ``test_bakir_can_grant_an_owl_a_capability.py`` precedent) that none of
    the six migrated actions' method bodies call a mutator by name — only
    ``submit_command``."""

    @pytest.mark.parametrize("method_name", ["_create", "_edit", "_edit_unbound", "_rename", "_retire"])
    def test_action_method_never_calls_persist_owl_or_registry_replace_directly(
        self, method_name: str,
    ) -> None:
        src = inspect.getsource(getattr(OwlBuildTool, method_name))
        assert "submit_command" in src, f"{method_name} never calls submit_command"
        for forbidden in ("persist_owl(", "registry.replace(", "registry.register(", "delete_owl("):
            assert forbidden not in src, (
                f"{method_name} still calls {forbidden!r} directly instead of "
                "going through submit_command"
            )

    def test_toggle_schedule_never_calls_job_scheduler_pause_resume_directly(self) -> None:
        src = inspect.getsource(OwlBuildTool._toggle_schedule)
        assert "submit_command" in src
        assert "scheduler.pause(" not in src
        assert "scheduler.resume(" not in src


class TestCreateEditRenameRetireSubmitTheirDeclaredType:
    @pytest.mark.asyncio
    async def test_create_submits_owls_build_create(
        self, tmp_path: Path, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = OwlRegistry.with_default_secretary()
        token = set_services(_services(tmp_db, registry))
        trace = _trace()
        calls = _spy_submit_command(monkeypatch)
        try:
            result = await OwlBuildTool().execute(
                action="create", name="scout", preset="researcher", specialty="recon",
            )
        finally:
            TraceContext.reset(trace)
            reset_services(token)

        assert result.success, result.error
        assert calls == [CREATE]
        assert registry.get("scout").origin == "agent"  # the real effect landed

    @pytest.mark.asyncio
    async def test_edit_submits_owls_build_edit(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = OwlRegistry.with_default_secretary()
        token = set_services(_services(tmp_db, registry))
        trace = _trace()
        try:
            created = await OwlBuildTool().execute(
                action="create", name="scout2", preset="researcher", specialty="recon",
            )
            assert created.success, created.error
            calls = _spy_submit_command(monkeypatch)
            result = await OwlBuildTool().execute(
                action="edit", name="scout2", specialty="updated recon role",
            )
        finally:
            TraceContext.reset(trace)
            reset_services(token)

        assert result.success, result.error
        assert calls == [EDIT]
        assert "updated recon role" in registry.get("scout2").role

    @pytest.mark.asyncio
    async def test_rename_submits_owls_build_rename(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = OwlRegistry.with_default_secretary()
        token = set_services(_services(tmp_db, registry))
        trace = _trace()
        try:
            calls = _spy_submit_command(monkeypatch)
            result = await OwlBuildTool().execute(
                action="rename", name="secretary", display_name="Ada",
            )
        finally:
            TraceContext.reset(trace)
            reset_services(token)

        assert result.success, result.error
        assert calls == [RENAME]
        assert registry.get("secretary").display_name == "Ada"

    @pytest.mark.asyncio
    async def test_retire_submits_owls_build_retire(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = OwlRegistry.with_default_secretary()
        token = set_services(_services(tmp_db, registry))
        trace = _trace()
        try:
            created = await OwlBuildTool().execute(
                action="create", name="scout3", preset="researcher", specialty="recon",
            )
            assert created.success, created.error
            calls = _spy_submit_command(monkeypatch)
            result = await OwlBuildTool().execute(action="retire", name="scout3")
        finally:
            TraceContext.reset(trace)
            reset_services(token)

        assert result.success, result.error
        assert calls == [RETIRE]
        from stackowl.exceptions import OwlNotFoundError

        with pytest.raises(OwlNotFoundError):  # genuinely gone
            registry.get("scout3")


class TestPauseResumeSubmitSchedulingTypes:
    @pytest.mark.asyncio
    async def test_pause_and_resume_submit_scheduling_owl_job_types(
        self, tmp_path: Path, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.scheduler.commands import PAUSE_OWL_JOB, RESUME_OWL_JOB

        registry = OwlRegistry()
        registry.register(
            OwlAgentManifest(
                name="watcher", role="w", system_prompt="p", model_tier="fast",
                lifecycle="scheduled", trigger=CronTrigger(schedule="every 10m", prompt="go"),
            ),
            source_name="t",
        )
        await reconcile_owl_schedules(registry, tmp_db)
        token = set_services(StepServices(owl_registry=registry, db_pool=tmp_db))
        calls = _spy_submit_command(monkeypatch)
        try:
            paused = await OwlBuildTool().execute(action="pause", name="watcher")
            assert paused.success, paused.error
            resumed = await OwlBuildTool().execute(action="resume", name="watcher")
            assert resumed.success, resumed.error
        finally:
            reset_services(token)

        assert calls == [PAUSE_OWL_JOB, RESUME_OWL_JOB]
        row = await tmp_db.fetch_all(
            "SELECT enabled FROM jobs WHERE job_id = ?", (_job_id_for("watcher"),),
        )
        assert int(row[0]["enabled"]) == 1  # resumed — the real effect landed
