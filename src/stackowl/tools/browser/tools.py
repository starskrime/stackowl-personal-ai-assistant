"""Atomic browser tools — agent-fluent surface over Camoufox + Playwright.

Every tool here is a stateless wrapper around a session in
:class:`BrowserSessionRegistry`. The LLM threads ``session_id`` (and optionally
``page_handle``) through tool calls to drive a multi-step flow. For one-shot
'fetch and extract' the LLM uses ``web_fetch`` (see :mod:`stackowl.tools.io.web_fetch`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from stackowl.infra import untrusted
from stackowl.infra.observability import log
from stackowl.pipeline import write_narrowing
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser._extraction import extract_links, extract_markdown
from stackowl.tools.browser._fingerprint import detect_captcha
from stackowl.tools.browser._logging import truncate_for_error, url_path_only
from stackowl.tools.browser._retry import with_browser_retry
from stackowl.tools.browser.sessions import (
    BrowserSessionLimitError,
    BrowserSessionNotFoundError,
)

_DEFAULT_NAV_TIMEOUT_MS = 30_000
_DEFAULT_SELECTOR_TIMEOUT_MS = 10_000

# Expected, recoverable failures from the browser engine + session layer. These
# are caught inside each tool's ``execute`` and surfaced as a structured
# ``ToolResult(success=False, ...)`` so the agent gets an actionable OBSERVATION
# (re-snapshot, retry, reopen session) instead of an exception bubbling to the
# base Tool wrapper. ``PlaywrightTimeout`` is a subclass of ``PlaywrightError``
# but is listed explicitly for clarity.
_BROWSER_ERRORS: tuple[type[BaseException], ...] = (
    PlaywrightTimeout,
    PlaywrightError,
    BrowserSessionNotFoundError,
    BrowserSessionLimitError,
)


# --------------------------------------------------------------------------- helpers


def _services_or_unavailable() -> tuple[Any, Any, str | None]:
    """Return (runtime, sessions, error_message). error_message is None on success.

    Does NOT short-circuit on ``runtime.available`` being False — the runtime
    self-heals on first ``open_context()`` / ``ensure_available()`` call, so
    a dead handle from a previous crash will recover transparently here.
    """
    services = get_services()
    runtime = services.browser_runtime
    sessions = services.browser_sessions
    if runtime is None or sessions is None:
        return None, None, "Browser runtime not initialized"
    return runtime, sessions, None


def _audit_consequential(event_type: str, target: str | None, details: dict[str, Any]) -> None:
    """Best-effort write of one consequential-browser-action audit row."""
    services = get_services()
    audit = services.audit_logger
    if audit is None:
        return
    try:
        audit.append(
            event_type=event_type,
            actor=details.get("owl_name") or "browser_tool",
            target=target,
            details=details,
        )
    except Exception as exc:
        log.tool.warning(
            f"audit.{event_type}: append failed",
            exc_info=exc,
            extra={"_fields": {"target": target}},
        )


def _err(msg: str, t0: float, *, tool: str = "browser_tool", committed: bool = True) -> ToolResult:
    """Structured failure. ``committed`` defaults True (conservative); callers pass
    False at a pre-execution refusal (runtime/session unavailable, arg-validation,
    missing local resource) reached BEFORE the page action runs, so it does not trip
    the give-up floor. A failure AFTER the page action was attempted keeps True."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        f"{tool}.execute: exit",
        extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
    )
    return ToolResult(
        success=False, output="", error=msg,
        duration_ms=duration_ms, side_effect_committed=committed,
    )


def _browser_failure(msg: str, exc: BaseException, t0: float, *, tool: str) -> ToolResult:
    """ERROR-log a caught Playwright/session exception (B5), then return a
    structured failure so the agent gets an actionable OBSERVATION instead of
    the exception bubbling to the base Tool wrapper."""
    log.tool.error(
        f"{tool}.execute: browser error — returning structured failure",
        exc_info=exc,
        extra={"_fields": {"tool": tool, "error_type": type(exc).__name__}},
    )
    return _err(msg, t0, tool=tool)


def _ok(payload: dict[str, Any] | list[Any] | str, t0: float, *, tool: str = "browser_tool") -> ToolResult:
    output = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        f"{tool}.execute: exit",
        extra={"_fields": {"success": True, "output_len": len(output), "duration_ms": duration_ms}},
    )
    return ToolResult(success=True, output=output, duration_ms=duration_ms)


def _owner_key_from_state() -> str:
    """The owner_key for this turn — see ``sessions.owner_key_for_turn``.

    THE OLD NOTE HERE WAS TRUE AND IS NOT ANY MORE. It said tools cannot read
    PipelineState ("_dispatch only forwards LLM kwargs. For v1 we use 'local'"),
    so this returned "local" unconditionally while
    ``commands/browser_command.py`` built ``telegram:{session_key}`` and believed
    it was mirroring this function. A session created by the TOOL was therefore
    owned by ``local`` and invisible to ``/browser sessions`` on Telegram.

    ``TraceContext`` carries ``session_key`` and ``channel`` as contextvars across
    async hops — the same mechanism ``PlanStore`` uses to key a plan to its lane —
    so the premise is gone and both callers now ask one implementation.
    """
    from stackowl.tools.browser.sessions import owner_key_for_turn

    return owner_key_for_turn()


# --------------------------------------------------------------------------- atomic tools


class _BrowserTool(Tool):
    """Base for atomic browser tools.

    All browser tools share ``toolset_group="browser"`` so a browser-profiled owl
    counts them as ONE capability group under the E1-S4 presented-set cap (else a
    browser owl would blow the cap on browser tools alone). Subclasses set the
    class attribute ``_severity`` instead of overriding ``manifest`` — the group is
    injected uniformly here.
    """

    _severity: Literal["read", "write", "consequential"] = "read"
    # ESC-9 — cold-start presentation weight. Same class-attribute pattern as
    # _severity so a subclass declares one line rather than overriding manifest.
    # A browser owl needs to SEE, NAVIGATE and ACT before it needs cookies or
    # eval_js; without this the budget cut by alphabet and took snapshot, type,
    # wait_for and the tab tools.
    _priority: int = 0
    _consent_category: str | None = None
    _commit_coupling: Literal["transactional", "idempotent_keyed", "unconfirmed"] | None = None
    #: D03.4 level 3 — the largest result this tool may return. None = uncapped.
    #: Set per subclass; only the tools measured to overflow declare one.
    _max_result_chars: int | None = None

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity=self._severity,
            presentation_priority=self._priority,
            commit_coupling=self._commit_coupling,
            consent_category=self._consent_category,
            toolset_group="browser",
            # D05.3 — every browser tool needs the Camoufox runtime. Declared on
            # the shared base so the 18 subclasses inherit it; the SEVEN browser
            # tools that subclass Tool directly (browse, snapshot, back, press,
            # get_images, console, dialog) each declare it themselves. Editing
            # only this base would have left those seven silently ungated.
            requires_capability="browser",
            max_result_size_chars=self._max_result_chars,
        )


class BrowserNavigateTool(_BrowserTool):
    _priority = 90
    @property
    def name(self) -> str: return "browser_navigate"
    @property
    def description(self) -> str:
        return (
            "Navigate a browser session to a URL. If session_id is omitted, a new session is opened. "
            "Returns {session_id, page_handle, final_url, title, status}."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "session_id": {"type": "string", "description": "Reuse an existing session (omit to open new)."},
                "profile_name": {"type": "string", "description": "Persistent profile name (for logged-in sites)."},
                "wait_for": {
                    "type": "string",
                    "enum": ["domcontentloaded", "load", "networkidle"],
                    "default": "domcontentloaded",
                },
                write_narrowing.PARAM: write_narrowing.PARAM_SCHEMA,
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        url = str(kwargs.get("url", ""))
        session_id = kwargs.get("session_id")
        profile_name = kwargs.get("profile_name")
        wait_for = str(kwargs.get("wait_for", "domcontentloaded"))
        log_url = url_path_only(url)
        log.tool.info(
            "browser_navigate.execute: entry",
            extra={"_fields": {"url": log_url, "session_id": session_id, "profile": profile_name}},
        )
        if not url:
            return _err("Missing url parameter", t0)
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)

        await runtime.acquire_domain_slot(url)

        async def _do() -> tuple[Any, Any, str, int, str, str | None]:
            sid_local = session_id
            if sid_local is None:
                sid_local = await sessions.acquire(
                    _owner_key_from_state(),
                    profile_name=str(profile_name) if profile_name else None,
                )
            try:
                sess_local, page_local, handle_local = await sessions.get_page(str(sid_local))
            except Exception:
                # Session may have been purged by a runtime recycle — reopen.
                sid_local = await sessions.acquire(
                    _owner_key_from_state(),
                    profile_name=str(profile_name) if profile_name else None,
                )
                sess_local, page_local, handle_local = await sessions.get_page(str(sid_local))
            resp = await page_local.goto(url, wait_until=wait_for, timeout=_DEFAULT_NAV_TIMEOUT_MS)
            await runtime.record_navigation()
            sess_local.nav_count += 1
            inner_status = resp.status if resp is not None else 0
            inner_title = await page_local.title()
            inner_captcha = await detect_captcha(page_local)
            return sid_local, page_local, handle_local, inner_status, inner_title, inner_captcha

        try:
            session_id, page, page_handle, status, title, captcha_kind = await with_browser_retry(
                _do, runtime, op_name="browser_navigate",
            )
        except Exception as exc:
            return _err(f"{type(exc).__name__}: {exc}", t0, tool="browser_navigate")
        log.tool.info(
            "browser_navigate.execute: exit",
            extra={"_fields": {
                "session_id": session_id, "page_handle": page_handle,
                "status": status, "captcha": captcha_kind,
            }},
        )
        return _ok({
            "session_id": session_id,
            "page_handle": page_handle,
            "final_url": url_path_only(page.url),
            # FENCED (D17.2): a page TITLE is chosen by the visited site, so it is
            # attacker-authored text arriving in the model's context. untrusted.py's
            # table names browser_navigate and the fence had never reached it.
            "title": untrusted.wrap(title, source=f"browser_navigate:{log_url}"),
            "status": status,
            "captcha_detected": captcha_kind,
        }, t0)


class BrowserExtractTool(_BrowserTool):
    #: D03.4 — MEASURED, not guessed. 267 real browser_extract results:
    #: p50 1,466 chars, p75 7,929, p90 92,090, p95 287,161, p99 1,076,938,
    #: MAX 4,201,658 — four times the whole context window.
    #: 200,000 sits above p90 and below p95, so ordinary pages are untouched and
    #: only genuinely huge ones are cut (7%, 19 of 267). It is also ~20% of the
    #: ~1M-char window, so one tool result cannot crowd out the history and the
    #: prompt. Nothing is lost: the full text spills to disk and the path is
    #: returned in the output.
    #: This tool produced ALL 26 results above 100k chars; it is the reason the
    #: item exists, and a cap nobody sets would make the whole mechanism dormant.
    _max_result_chars = 200_000

    _priority = 70
    @property
    def name(self) -> str: return "browser_extract"
    @property
    def description(self) -> str:
        return (
            "Extract, scrape and read the visible content or article text of the current web page, so it can be "
            "summarised or searched. Modes: markdown (default, via Trafilatura), text, links, html."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "mode": {"type": "string", "enum": ["markdown", "text", "links", "html"], "default": "markdown"},
                "selector": {"type": "string", "description": "Optional CSS selector to scope extraction."},
                write_narrowing.PARAM: write_narrowing.PARAM_SCHEMA,
            },
            "required": ["session_id"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        mode = str(kwargs.get("mode", "markdown"))
        selector = kwargs.get("selector")
        log.tool.info(
            "browser_extract.execute: entry",
            extra={"_fields": {"session_id": session_id, "mode": mode, "selector": selector}},
        )
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            if selector:
                html = await page.evaluate(
                    "(s) => { const el = document.querySelector(s); return el ? el.outerHTML : ''; }", str(selector)
                )
            else:
                html = await page.content()
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Extract failed (page may have navigated/closed — re-run browser_navigate): "
                f"{type(exc).__name__}: {exc}",
                exc, t0, tool="browser_extract",
            )
        if mode == "html":
            output = html or ""
        elif mode == "links":
            links = extract_links(html or "", base_url=page.url)
            output = "\n".join(f"- [{li['text'] or li['href']}]({li['href']})" for li in links)
        elif mode == "text":
            output = extract_markdown(html or "", include_links=False)
        else:
            output = extract_markdown(html or "", include_links=True)
        log.tool.info(
            "browser_extract.execute: exit",
            extra={"_fields": {"session_id": session_id, "output_len": len(output)}},
        )
        # FENCED AS DATA. Whatever the page says, it is not an instruction to this
        # agent. MEASURED 2026-09-03: browser_extract shared a turn with shell 13
        # times and with write_file 7 times in 7 days, and the marker that pdf.py
        # has always applied reached none of those turns.
        return _ok(untrusted.wrap(output, source=f"browser_extract:{page.url}"), t0)


class BrowserClickTool(_BrowserTool):
    _priority = 90
    _severity = "write"
    _commit_coupling = "unconfirmed"
    # Engine-emitted aria refs are opaque alphanumeric tokens (e.g. "e7"). We
    # validate the shape before interpolating into the aria-ref selector so a
    # ref value can never inject selector syntax (E2-S1 click-by-ref).
    _REF_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

    @property
    def name(self) -> str: return "browser_click"
    @property
    def description(self) -> str:
        return (
            "Click an element. Preferred: pass ref=<id> from a browser_snapshot [ref=eN] marker "
            "(stable across re-render). Otherwise pass selector_or_text (CSS selector tried first, "
            "then visible text)."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "ref": {"type": "string", "description": "Stable ref from browser_snapshot (e.g. 'e7')."},
                "selector_or_text": {"type": "string"},
            },
            "required": ["session_id"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        ref = str(kwargs.get("ref", "")).strip()
        target = str(kwargs.get("selector_or_text", ""))
        log.tool.info(
            "browser_click.execute: entry",
            extra={"_fields": {"session_id": session_id, "by_ref": bool(ref), "target_len": len(target)}},
        )
        if not ref and not target:
            return _err(
                "Provide either ref (from browser_snapshot) or selector_or_text",
                t0, tool="browser_click", committed=False,
            )
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_click", committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"browser session unavailable (reopen with browser_navigate): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_click",
            )
        # Preferred path: click by snapshot ref via the engine's aria-ref selector engine.
        if ref:
            if not self._REF_PATTERN.match(ref):
                return _err(f"Invalid ref format: {ref!r}", t0, tool="browser_click", committed=False)
            try:
                await page.locator(f"aria-ref={ref}").click(timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
            except Exception as exc:
                return _err(
                    f"Click by ref {ref!r} failed (stale snapshot? re-run browser_snapshot): {exc}",
                    t0, tool="browser_click",
                )
            log.tool.info("browser_click.execute: exit", extra={"_fields": {"mode": "ref"}})
            return _ok({"ok": True, "click_target": "ref"}, t0, tool="browser_click")
        # Fallback path: CSS selector first, then visible text.
        try:
            await page.click(target, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
            mode = "selector"
        except Exception:
            try:
                await page.get_by_text(target, exact=False).first.click(timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
                mode = "text"
            except Exception as exc:
                return _err(f"Click failed: {exc}", t0, tool="browser_click")
        log.tool.info("browser_click.execute: exit", extra={"_fields": {"mode": mode}})
        return _ok({"ok": True, "click_target": mode}, t0, tool="browser_click")


class BrowserTypeTool(_BrowserTool):
    _priority = 90
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_type"
    @property
    def description(self) -> str:
        return "Type text into an input field identified by CSS selector. Set submit=true to press Enter after."
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "submit": {"type": "boolean", "default": False},
            },
            "required": ["session_id", "selector", "text"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        selector = str(kwargs.get("selector", ""))
        text = str(kwargs.get("text", ""))
        submit = bool(kwargs.get("submit", False))
        log.tool.info(
            "browser_type.execute: entry",
            extra={"_fields": {
                "session_id": session_id,
                "selector_len": len(selector),
                "text_len": len(text),
                "submit": submit,
            }},
        )
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            await page.fill(selector, text, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
            if submit:
                await page.press(selector, "Enter")
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Type failed for selector {selector!r} (element not found / stale snapshot — "
                f"re-run browser_snapshot): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_type",
            )
        return _ok({"ok": True}, t0, tool="browser_type")


class BrowserScreenshotTool(_BrowserTool):
    _priority = 60
    @property
    def name(self) -> str: return "browser_screenshot"
    @property
    def description(self) -> str:
        return "Take a screenshot of the current page (or a specific element). Returns the file path."
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "full_page": {"type": "boolean", "default": False},
                "selector": {"type": "string", "description": "Optional CSS selector to capture a specific element."},
            },
            "required": ["session_id"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        full_page = bool(kwargs.get("full_page", False))
        selector = kwargs.get("selector")
        log.tool.info(
            "browser_screenshot.execute: entry",
            extra={"_fields": {"session_id": session_id, "full_page": full_page}},
        )
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            out_dir: Path = runtime.settings.screenshots_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            out_path = out_dir / f"{session_id[:8]}-{ts}.png"
            if selector:
                handle = await page.query_selector(str(selector))
                if handle is None:
                    return _err(f"Selector not found: {selector}", t0, tool="browser_screenshot")
                await handle.screenshot(path=str(out_path))
            else:
                await page.screenshot(path=str(out_path), full_page=full_page)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Screenshot failed (page may have navigated/closed): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_screenshot",
            )
        with contextlib.suppress(OSError):
            out_path.chmod(0o600)
        return _ok({"path": str(out_path)}, t0)


class BrowserScrollTool(_BrowserTool):
    # Part of SEEING the page: a snapshot only covers what is in view.
    _priority = 70
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_scroll"
    @property
    def description(self) -> str:
        return "Scroll the current page. direction in {down, up, top, bottom}; amount in {page, half, <pixels>}."
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "direction": {"type": "string", "enum": ["down", "up", "top", "bottom"], "default": "down"},
                "amount": {"type": "string", "default": "page"},
            },
            "required": ["session_id"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        direction = str(kwargs.get("direction", "down"))
        amount = str(kwargs.get("amount", "page"))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            if direction == "top":
                await page.evaluate("() => window.scrollTo(0, 0)")
            elif direction == "bottom":
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            else:
                sign = -1 if direction == "up" else 1
                if amount == "page":
                    px = "window.innerHeight"
                elif amount == "half":
                    px = "window.innerHeight / 2"
                else:
                    try:
                        px = str(int(amount))
                    except ValueError:
                        return _err(f"Invalid scroll amount: {amount}", t0, tool="browser_scroll", committed=False)
                await page.evaluate(f"() => window.scrollBy(0, {sign} * ({px}))")
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Scroll failed (page may have navigated/closed): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_scroll",
            )
        return _ok({"ok": True}, t0, tool="browser_scroll")


class BrowserWaitForTool(_BrowserTool):
    _priority = 70
    @property
    def name(self) -> str: return "browser_wait_for"
    @property
    def description(self) -> str:
        return "Wait for an element matching the CSS selector to appear (or be visible). Timeout in ms."
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "selector": {"type": "string"},
                "timeout_ms": {"type": "integer", "default": _DEFAULT_SELECTOR_TIMEOUT_MS},
            },
            "required": ["session_id", "selector"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        selector = str(kwargs.get("selector", ""))
        timeout_raw = kwargs.get("timeout_ms", _DEFAULT_SELECTOR_TIMEOUT_MS)
        timeout_ms = int(timeout_raw) if isinstance(timeout_raw, int | str) else _DEFAULT_SELECTOR_TIMEOUT_MS
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"browser session unavailable (reopen with browser_navigate): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_wait_for",
            )
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Timeout waiting for {selector!r}: {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_wait_for",
            )
        return _ok({"ok": True}, t0, tool="browser_wait_for")


class BrowserEvalJsTool(_BrowserTool):
    _severity = "consequential"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_eval_js"
    @property
    def description(self) -> str:
        return (
            "Evaluate JavaScript in the current page. Returns the JSON-serializable result. "
            "CONSEQUENTIAL — arbitrary code execution."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "script": {"type": "string"},
            },
            "required": ["session_id", "script"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        script = str(kwargs.get("script", ""))
        script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()[:16]
        # Never log the script content itself — only length + SHA prefix.
        log.tool.info(
            "browser_eval_js.execute: entry",
            extra={"_fields": {
                "session_id": session_id,
                "script_len": len(script),
                "script_sha256_prefix": script_sha256,
            }},
        )
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            result = await page.evaluate(script)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"eval_js failed (page may have navigated/closed, or script raised): "
                f"{type(exc).__name__}: {exc}",
                exc, t0, tool="browser_eval_js",
            )
        try:
            payload = json.dumps(result, default=str)
        except (TypeError, ValueError):
            payload = repr(result)
        _audit_consequential(
            "browser_eval_js",
            url_path_only(page.url),
            {"session_id": session_id, "script_len": len(script), "script_sha256_prefix": script_sha256},
        )
        return _ok(payload, t0)


class BrowserUploadTool(_BrowserTool):
    _severity = "consequential"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_upload"
    @property
    def description(self) -> str:
        return (
            "Set files on a file <input> element identified by CSS selector. "
            "CONSEQUENTIAL — touches local filesystem."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "selector": {"type": "string"},
                "file_path": {"type": "string"},
            },
            "required": ["session_id", "selector", "file_path"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        selector = str(kwargs.get("selector", ""))
        file_path = Path(str(kwargs.get("file_path", "")))
        if not file_path.exists():
            return _err(f"File not found: {file_path}", t0, committed=False)
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            await page.set_input_files(selector, str(file_path))
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Upload failed for selector {selector!r} (not a file input / element not found): "
                f"{type(exc).__name__}: {exc}",
                exc, t0, tool="browser_upload",
            )
        _audit_consequential(
            "browser_upload",
            url_path_only(page.url),
            {"session_id": session_id, "selector_len": len(selector), "file_path": str(file_path)},
        )
        return _ok({"ok": True}, t0)


class BrowserDownloadTool(_BrowserTool):
    _severity = "consequential"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_download"
    @property
    def description(self) -> str:
        return (
            "Download and save a file from a web page by clicking trigger_selector, "
            "capturing the resulting attachment such as a PDF, CSV, image or export. "
            "Returns {path, bytes, sha256}. CONSEQUENTIAL."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "trigger_selector": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 10_485_760},
            },
            "required": ["session_id", "trigger_selector"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        trigger = str(kwargs.get("trigger_selector", ""))
        max_bytes_raw = kwargs.get("max_bytes", 10_485_760)
        max_bytes = int(max_bytes_raw) if isinstance(max_bytes_raw, int | str) else 10_485_760
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess, page, _ph = await sessions.get_page(session_id, str(page_handle) if page_handle else None)
            downloads_dir: Path = runtime.settings.downloads_dir
            downloads_dir.mkdir(parents=True, exist_ok=True)
            async with page.expect_download() as di:
                await page.click(trigger)
            download = await di.value
            suggested = download.suggested_filename or f"download-{uuid.uuid4().hex[:8]}"
            out_path = downloads_dir / suggested
            await download.save_as(str(out_path))
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"Download failed: trigger {trigger!r} did not start a download "
                f"(element not found / no download fired): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_download",
            )
        with contextlib.suppress(OSError):
            out_path.chmod(0o600)
        size = out_path.stat().st_size
        if size > max_bytes:
            out_path.unlink(missing_ok=True)
            return _err(f"Download exceeded max_bytes ({size} > {max_bytes})", t0)
        sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
        _audit_consequential(
            "browser_download",
            url_path_only(page.url),
            {"session_id": session_id, "bytes": size, "sha256": sha, "stored_path": str(out_path)},
        )
        return _ok({"path": str(out_path), "bytes": size, "sha256": sha}, t0)


class BrowserCookiesGetTool(_BrowserTool):
    @property
    def name(self) -> str: return "browser_cookies_get"
    @property
    def description(self) -> str:
        return (
            "Read, inspect and export the browser session's cookies, optionally filtered by domain. Use to capture a "
            "login or check which authentication state is active."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "domain": {"type": "string"},
            },
            "required": ["session_id"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        domain = kwargs.get("domain")
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            sess = await sessions.get(session_id)
            cookies = await sess.context.cookies()
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"cookies_get failed (session unavailable): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_cookies_get",
            )
        if domain:
            cookies = [c for c in cookies if str(domain) in c.get("domain", "")]
        return _ok(cookies, t0, tool="browser_cookies_get")


class BrowserCookiesSetTool(_BrowserTool):
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_cookies_set"
    @property
    def description(self) -> str:
        return (
            "Add, set, inject or restore cookies into the browser session from a list of cookie dicts. Use to reuse a "
            "saved login and authenticate without signing in again."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "cookies": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["session_id", "cookies"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        cookies = kwargs.get("cookies", [])
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        cookies_list = list(cookies) if isinstance(cookies, list) else []
        try:
            sess = await sessions.get(session_id)
            await sess.context.add_cookies(cookies_list)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"cookies_set failed (session unavailable / invalid cookie shape): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_cookies_set",
            )
        _audit_consequential(
            "browser_cookies_set",
            None,
            {"session_id": session_id, "cookie_count": len(cookies_list)},
        )
        return _ok({"ok": True}, t0)


class BrowserCookiesClearTool(_BrowserTool):
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_cookies_clear"
    @property
    def description(self) -> str:
        return (
            "Clear, delete and remove all cookies from the browser session. Use to log out, sign out, drop a stale "
            "login, "
            "or reset authentication state before a fresh visit."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess = await sessions.get(session_id)
            await sess.context.clear_cookies()
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"cookies_clear failed (session unavailable): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_cookies_clear",
            )
        _audit_consequential("browser_cookies_clear", None, {"session_id": session_id})
        return _ok({"ok": True}, t0, tool="browser_cookies_clear")


class BrowserTabOpenTool(_BrowserTool):
    @property
    def name(self) -> str: return "browser_tab_open"
    @property
    def description(self) -> str:
        return (
            "Open a new tab or page inside the existing browser session, so several sites can be visited side by side. "
            "Returns the new page_handle."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            # THE one caller that genuinely wants a new tab — now stated, not
            # inherited from "no handle happens to create one".
            sess, page, page_handle = await sessions.get_page(
                session_id, None, new_page=True
            )
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"tab_open failed (session unavailable / page limit reached): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_tab_open",
            )
        return _ok({"page_handle": page_handle}, t0, tool="browser_tab_open")


class BrowserTabListTool(_BrowserTool):
    @property
    def name(self) -> str: return "browser_tab_list"
    @property
    def description(self) -> str:
        return (
            "List and show every open tab or page in the browser session with its handle and current URL. Use to see "
            "what "
            "is open before switching or closing a tab."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0)
        try:
            sess = await sessions.get(session_id)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"tab_list failed (session unavailable): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_tab_list",
            )
        tabs = [{"page_handle": h, "url": url_path_only(p.url)} for h, p in sess.pages.items()]
        return _ok(tabs, t0, tool="browser_tab_list")


class BrowserTabCloseTool(_BrowserTool):
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_tab_close"
    @property
    def description(self) -> str:
        return (
            "Close one single tab or page inside the browser session while leaving the session and its other tabs "
            "open. "
            "Use to tidy up after finishing with one page."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"session_id": {"type": "string"}, "page_handle": {"type": "string"}},
            "required": ["session_id", "page_handle"],
        }


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        page_handle = str(kwargs.get("page_handle", ""))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            sess = await sessions.get(session_id)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"tab_close failed (session unavailable): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_tab_close",
            )
        page = sess.pages.pop(page_handle, None)
        # Drop the page's console/error buffers and cancel any armed dialog TTL
        # timers so they don't fire dismiss() on a closed page.
        closed_obs = sess.observers.pop(page_handle, None)
        if closed_obs is not None:
            sessions._cancel_dialog_timers(closed_obs)
        if page is None:
            return _err(f"page_handle not found: {page_handle}", t0)
        with contextlib.suppress(Exception):
            await page.close()
        return _ok({"ok": True}, t0)


class BrowserCloseTool(_BrowserTool):
    _severity = "write"
    _commit_coupling = "unconfirmed"
    @property
    def name(self) -> str: return "browser_close"
    @property
    def description(self) -> str:
        return (
            "Close, quit and shut down an entire browser session, closing every open tab and releasing its context and "
            "memory. Use when finished browsing a site."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}


    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        session_id = str(kwargs.get("session_id", ""))
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, committed=False)
        try:
            await sessions.close(session_id)
        except _BROWSER_ERRORS as exc:
            return _browser_failure(
                f"close failed (session may already be gone): {type(exc).__name__}: {exc}",
                exc, t0, tool="browser_close",
            )
        return _ok({"ok": True}, t0, tool="browser_close")


class BrowserRecallUrlTool(_BrowserTool):
    @property
    def name(self) -> str: return "browser_recall_url"
    @property
    def description(self) -> str:
        return (
            "Check whether anything was written down about a URL in curated "
            "memory. Returns {found, notes?}. NOT a page cache — it answers "
            "'have I noted anything about this', never 'here is the page'."
        )
    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

    async def execute(self, **kwargs: object) -> ToolResult:
        """Search CURATED memory for a note mentioning this URL (ESC-3).

        This used to read ``committed_facts`` through the memory bridge, which
        has held 0 rows since D08.1's migration 0112 and lost its writers when
        web_fetch stopped staging pages — so it could only ever answer
        ``{"found": false}``. Bakir chose to keep the tool and repoint it rather
        than remove it.

        The promise narrows honestly with the source. Curated memory is two
        small text files under a hard character budget, holding what the agent
        chose to write down — not a page cache. So this answers "have I noted
        anything about this URL", never "here is the page I fetched".
        """
        # 1. ENTRY
        t0 = time.monotonic()
        url = str(kwargs.get("url", ""))
        # Match on host+path: what someone writes in a note ("the runbook is at
        # example.com/ops") rarely carries the scheme the caller passes in.
        needle = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url).rstrip("/")
        # 2. DECISION
        if not needle:
            return _err("browser_recall_url requires a 'url'.", t0)
        try:
            from stackowl.memory.curated import shared_memory

            hits = shared_memory().search(needle)
            if not hits:
                # Fall back to the path alone: a note may name the page without
                # the host ("/ops is the runbook").
                path = url_path_only(url)
                if path and path != needle:
                    hits = shared_memory().search(path)
        except Exception as exc:  # B5 — a lookup must not cost the turn
            log.tool.error(
                "[browser] recall_url: curated search FAILED",
                exc_info=exc, extra={"_fields": {"url": url}},
            )
            return _err(
                f"Curated memory search failed: {truncate_for_error(str(exc))}", t0
            )
        # 3. STEP + 4. EXIT
        if not hits:
            log.tool.info(
                "[browser] recall_url: exit — nothing noted",
                extra={"_fields": {"needle": needle}},
            )
            return _ok({"found": False}, t0)
        log.tool.info(
            "[browser] recall_url: exit — found",
            extra={"_fields": {"needle": needle, "hits": len(hits)}},
        )
        return _ok({
            "found": True,
            "notes": [f"[{target}] {text}" for target, text in hits][:5],
        }, t0)


# --------------------------------------------------------------------------- registry helper


ATOMIC_BROWSER_TOOLS: tuple[type[Tool], ...] = (
    BrowserNavigateTool,
    BrowserExtractTool,
    BrowserClickTool,
    BrowserTypeTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserWaitForTool,
    BrowserEvalJsTool,
    BrowserUploadTool,
    BrowserDownloadTool,
    BrowserCookiesGetTool,
    BrowserCookiesSetTool,
    BrowserCookiesClearTool,
    BrowserTabOpenTool,
    BrowserTabListTool,
    BrowserTabCloseTool,
    BrowserCloseTool,
    BrowserRecallUrlTool,
)
