"""Tests for browser_dialog (E2-S6) + the sessions.py dialog queue substrate.

Uses the REAL BrowserSessionRegistry with a fake runtime/context/page so the
eager page.on("dialog") wiring, bounded queue, and TTL auto-dismiss are exercised.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from stackowl.config.browser import BrowserSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.dialog import BrowserDialogTool
from stackowl.tools.browser.sessions import _DIALOG_QUEUE_MAX, BrowserSessionRegistry


class _FakeDialog:
    def __init__(self, type_: str = "confirm", message: str = "Are you sure?", default: str = "") -> None:
        self.type = type_
        self.message = message
        self.default_value = default
        self.accepted_with: list[str | None] = []
        self.dismissed = False

    async def accept(self, prompt_text: str | None = None) -> None:
        self.accepted_with.append(prompt_text)

    async def dismiss(self) -> None:
        self.dismissed = True


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://x.test/"
        self._handlers: dict[str, Callable[[Any], None]] = {}

    def on(self, event: str, cb: Callable[[Any], None]) -> None:
        self._handlers[event] = cb

    def fire_dialog(self, dialog: _FakeDialog) -> None:
        self._handlers["dialog"](dialog)


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        pass

    async def route(self, pattern: str, handler: Any) -> None:
        pass  # FX-05 — real BrowserContext.route(); this suite doesn't assert wiring.


class _FakeRuntime:
    available = True

    async def open_context(self, **kwargs: Any) -> _FakeContext:
        return _FakeContext()

    def register_on_recycled(self, cb: Any) -> None:
        pass


def _settings(tmp_path: Any, *, ttl: float = 60.0) -> BrowserSettings:
    return BrowserSettings(
        max_concurrent_sessions=3,
        max_concurrent_pages_per_session=3,
        session_idle_timeout_minutes=30,
        dialog_auto_dismiss_seconds=ttl,
        profiles_dir=tmp_path / "p",
        screenshots_dir=tmp_path / "s",
        downloads_dir=tmp_path / "d",
        browser_cache_dir=tmp_path / "c",
    )


async def _open(settings: BrowserSettings) -> tuple[BrowserSessionRegistry, str, str, _FakePage]:
    reg = BrowserSessionRegistry(_FakeRuntime(), settings)  # type: ignore[arg-type]
    sid = await reg.open("local")
    _sess, page, handle = await reg.get_page(sid)
    return reg, sid, handle, page


class TestDialogSubstrate:
    async def test_dialog_captured_with_metadata(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        page.fire_dialog(_FakeDialog(type_="confirm", message="Delete?"))
        assert len(sess.observers[handle].dialogs) == 1
        pd = next(iter(sess.observers[handle].dialogs.values()))
        assert pd.type == "confirm"
        assert pd.message == "Delete?"
        assert pd.dialog_id

    async def test_queue_is_bounded_oldest_auto_dismissed(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        dialogs = [_FakeDialog(message=f"d{i}") for i in range(_DIALOG_QUEUE_MAX + 3)]
        for d in dialogs:
            page.fire_dialog(d)
        await asyncio.sleep(0.02)  # let the oldest-dismiss tasks run
        assert len(sess.observers[handle].dialogs) == _DIALOG_QUEUE_MAX
        assert dialogs[0].dismissed is True  # oldest auto-dismissed to make room

    async def test_ttl_auto_dismiss(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path, ttl=0.05))
        sess = await reg.get(sid)
        d = _FakeDialog()
        page.fire_dialog(d)
        assert len(sess.observers[handle].dialogs) == 1
        await asyncio.sleep(0.12)  # past the TTL
        assert d.dismissed is True
        assert len(sess.observers[handle].dialogs) == 0  # removed after auto-dismiss

    async def test_session_close_cancels_timers(self, tmp_path: Any) -> None:
        # M1 reproducer: close must cancel armed TTL timers so they never fire
        # dismiss() on a dead page.
        reg, sid, handle, page = await _open(_settings(tmp_path, ttl=0.05))
        d = _FakeDialog()
        page.fire_dialog(d)
        await reg.close(sid)
        await asyncio.sleep(0.12)  # well past the TTL
        assert d.dismissed is False  # leaked timer did NOT fire on the closed page

    async def test_tab_close_cancels_timers(self, tmp_path: Any) -> None:
        from stackowl.pipeline.services import StepServices, reset_services, set_services
        from stackowl.tools.browser.tools import BrowserTabCloseTool

        reg, sid, handle, page = await _open(_settings(tmp_path, ttl=0.05))
        sess = await reg.get(sid)
        d = _FakeDialog()
        page.fire_dialog(d)
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            await BrowserTabCloseTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        await asyncio.sleep(0.12)
        assert d.dismissed is False  # timer cancelled, did not fire post-close
        assert handle not in sess.observers


class TestBrowserDialogTool:
    def test_manifest_consequential_and_grouped(self) -> None:
        m = BrowserDialogTool().manifest
        assert m.action_severity == "consequential"
        assert m.toolset_group == "browser"
        assert m.name == "browser_dialog"

    def test_is_always_ask(self) -> None:
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_TOOLS

        assert "browser_dialog" in _DEFAULT_ALWAYS_ASK_TOOLS

    async def test_accept_confirm(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        d = _FakeDialog(type_="confirm")
        page.fire_dialog(d)
        did = next(iter(sess.observers[handle].dialogs))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="accept", dialog_id=did
            )
        finally:
            reset_services(token)
        assert result.success is True
        assert d.accepted_with == [None]  # confirm accepted without prompt text
        assert did not in sess.observers[handle].dialogs  # resolved + popped

    async def test_accept_prompt_with_text(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        d = _FakeDialog(type_="prompt", message="Name?", default="anon")
        page.fire_dialog(d)
        did = next(iter(sess.observers[handle].dialogs))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="accept", dialog_id=did, prompt_text="Alice"
            )
        finally:
            reset_services(token)
        assert d.accepted_with == ["Alice"]

    async def test_dismiss(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        d = _FakeDialog()
        page.fire_dialog(d)
        did = next(iter(sess.observers[handle].dialogs))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="dismiss", dialog_id=did
            )
        finally:
            reset_services(token)
        assert result.success is True
        assert d.dismissed is True
        assert did not in sess.observers[handle].dialogs

    async def test_resolve_cancels_ttl_no_double_action(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path, ttl=0.05))
        sess = await reg.get(sid)
        d = _FakeDialog(type_="confirm")
        page.fire_dialog(d)
        did = next(iter(sess.observers[handle].dialogs))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            await BrowserDialogTool().execute(session_id=sid, page_handle=handle, action="accept", dialog_id=did)
        finally:
            reset_services(token)
        await asyncio.sleep(0.12)  # past the TTL
        assert d.accepted_with == [None]  # accepted exactly once
        assert d.dismissed is False  # TTL timer was cancelled — no double action

    async def test_accept_failure_pops_dialog(self, tmp_path: Any) -> None:
        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)

        class _BoomDialog(_FakeDialog):
            async def accept(self, prompt_text: str | None = None) -> None:
                raise RuntimeError("engine closed")

        d = _BoomDialog()
        page.fire_dialog(d)
        did = next(iter(sess.observers[handle].dialogs))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="accept", dialog_id=did
            )
        finally:
            reset_services(token)
        assert result.success is False
        assert "failed" in (result.error or "")
        assert did not in sess.observers[handle].dialogs  # popped despite failure

    async def test_unknown_dialog_id(self, tmp_path: Any) -> None:
        reg, sid, handle, _page = await _open(_settings(tmp_path))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="accept", dialog_id="nope"
            )
        finally:
            reset_services(token)
        assert result.success is False
        assert "Unknown" in (result.error or "")

    async def test_invalid_action(self, tmp_path: Any) -> None:
        reg, sid, handle, _page = await _open(_settings(tmp_path))
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserDialogTool().execute(
                session_id=sid, page_handle=handle, action="explode", dialog_id="x"
            )
        finally:
            reset_services(token)
        assert result.success is False
        assert "Invalid action" in (result.error or "")

    async def test_no_runtime_unavailable(self) -> None:
        token = set_services(StepServices())
        try:
            result = await BrowserDialogTool().execute(session_id="s1", action="dismiss", dialog_id="x")
        finally:
            reset_services(token)
        assert result.success is False

    def test_registered_and_severity_enforced(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        reg = ToolRegistry.with_defaults()
        tool = reg.get("browser_dialog")
        assert tool is not None
        assert tool.manifest.action_severity == "consequential"


class TestSnapshotSurfacesDialogs:
    async def test_pending_dialog_in_snapshot(self, tmp_path: Any) -> None:
        from stackowl.tools.browser.snapshot import BrowserSnapshotTool

        reg, sid, handle, page = await _open(_settings(tmp_path))
        sess = await reg.get(sid)
        page.fire_dialog(_FakeDialog(type_="alert", message="Heads up"))
        did = next(iter(sess.observers[handle].dialogs))

        # Give the fake page an aria_snapshot so BrowserSnapshotTool can run.
        async def _aria_snapshot(*, mode: str | None = None, depth: int | None = None) -> str:
            return '- generic [ref=e1]'

        class _Loc:
            async def aria_snapshot(self, *, mode: str | None = None, depth: int | None = None) -> str:
                return await _aria_snapshot(mode=mode, depth=depth)

        page.locator = lambda sel: _Loc()  # type: ignore[attr-defined]
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserSnapshotTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        assert result.success is True
        assert did in result.output  # dialog_id surfaced for the model to act on
        assert "Heads up" in result.output
