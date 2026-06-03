"""BrowserBrowseTool — inner-LLM meta-tool that drives the browser autonomously.

Modeled on the browser-use pattern: DOM-with-indices perception + structured
action JSON from an inner LLM call. One audit row per browse via BatchAuditLogger.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from typing import Any

from stackowl.audit.batch_logger import BatchAuditLogger
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.providers.base import Message
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser._extraction import extract_markdown, index_dom_elements
from stackowl.tools.browser._fingerprint import detect_captcha, is_domain_allowed
from stackowl.tools.browser._logging import truncate_for_error, url_path_only

_DEFAULT_NAV_TIMEOUT_MS = 30_000
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)
# Consecutive identical (page-state, action) repeats that count as "stuck".
# The streak counts repeats after the first occurrence, so the loop breaks on
# the (_NO_PROGRESS_LIMIT + 1)-th identical step.
_NO_PROGRESS_LIMIT = 3


def _extract_action_json(reply: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of the inner LLM's reply. Best-effort."""
    candidates: list[str] = []
    for m in _FENCE_RE.finditer(reply):
        candidates.append(m.group(1).strip())
    candidates.append(reply.strip())
    for raw in candidates:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "action" in value:
            return value
    return None


def _system_prompt(task: str, allowed_domains: list[str]) -> str:
    return f"""You are a browser-driving agent. Your task:

    {task}

Allowed domains: {allowed_domains or '(any)'}

You receive page state on each turn. Reply with EXACTLY ONE JSON object
describing your next action. Use a fenced ```json block. Available actions:

  {{"action": "navigate", "url": "https://..."}}
  {{"action": "click_index", "index": <int>}}
  {{"action": "type_into_index", "index": <int>, "text": "...", "submit": false}}
  {{"action": "scroll", "direction": "down|up", "amount": "page"}}
  {{"action": "extract"}}                  — get clean markdown of current page
  {{"action": "screenshot"}}                — capture full-page PNG
  {{"action": "wait", "ms": 1500}}
  {{"action": "done", "summary": "..."}}    — finished; explain what you found

Rules:
- Prefer click_index/type_into_index over guessing CSS selectors.
- Only navigate to URLs whose host is in the allowed domains.
- When you have the answer, emit {{"action": "done", "summary": "..."}}.
- If you get stuck after a few attempts, emit done with status in the summary.
"""


def _page_state_text(url: str, title: str, elements: list[dict[str, Any]], snippet: str) -> str:
    lines = [f"URL: {url}", f"TITLE: {title}", "", "INTERACTIVE ELEMENTS (use the index for click/type):"]
    for el in elements:
        idx = el.get("index")
        tag = el.get("tag", "")
        text = (el.get("text") or "").strip()[:60]
        href = (el.get("href") or "").strip()[:60]
        name = (el.get("name") or "").strip()
        bits = [tag]
        if text:
            bits.append(f'"{text}"')
        if href:
            bits.append(f"→ {href}")
        if name:
            bits.append(f"name={name}")
        lines.append(f"  [{idx}] " + " ".join(bits))
    lines.append("")
    lines.append("PAGE TEXT (first 1500 chars):")
    lines.append(snippet[:1500])
    return "\n".join(lines)


class BrowserBrowseTool(Tool):
    @property
    def name(self) -> str:
        return "browser_browse"

    @property
    def description(self) -> str:
        return (
            "Run an inner LLM agent that drives the browser autonomously to complete a high-level task. "
            "Returns a transcript of actions, URLs visited, and the final summary. "
            "CONSEQUENTIAL — uses provider budget and may submit forms within allowed_domains."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Natural-language description of what the inner agent should accomplish.",
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hard allowlist of hostnames (suffix match). Empty means allow any.",
                },
                "max_steps": {"type": "integer", "default": 20},
                "session_id": {"type": "string", "description": "Reuse an existing session."},
                "seed_url": {"type": "string", "description": "Optional starting URL."},
            },
            "required": ["task", "allowed_domains"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="consequential",
            toolset_group="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        task = str(kwargs.get("task", ""))
        allowed_domains_raw = kwargs.get("allowed_domains", [])
        allowed_domains = [str(d) for d in allowed_domains_raw] if isinstance(allowed_domains_raw, list) else []
        max_steps_raw = kwargs.get("max_steps", 0)
        max_steps = int(max_steps_raw) if isinstance(max_steps_raw, int | str) else 0
        seed_url = kwargs.get("seed_url")
        existing_session_id = kwargs.get("session_id")

        if not task:
            return _err("Missing 'task' parameter", t0)

        services = get_services()
        runtime = services.browser_runtime
        sessions = services.browser_sessions
        providers = services.provider_registry
        if runtime is None or sessions is None:
            return _err("Browser runtime not initialized", t0)
        if providers is None:
            return _err("Provider registry not initialized — inner LLM loop cannot run", t0)

        # Resolve config defaults.
        s = runtime.settings
        if max_steps <= 0:
            max_steps = s.inner_browse_max_steps

        log.tool.info(
            "browser_browse.execute: entry",
            extra={"_fields": {
                "task_len": len(task),
                "allowed_domains": allowed_domains,
                "max_steps": max_steps,
                "session_id": existing_session_id,
            }},
        )
        try:
            inner_provider = providers.get_by_tier(s.inner_browse_model_tier)
        except Exception as exc:
            return _err(f"No provider available for tier {s.inner_browse_model_tier}: {exc}", t0)

        # Seed URL validation.
        if seed_url is not None and not is_domain_allowed(str(seed_url), allowed_domains):
            return _err(
                f"seed_url {url_path_only(str(seed_url))} not in allowed_domains {allowed_domains}",
                t0,
            )

        # Acquire / open session.
        owner_key = "local"  # See note in tools.py — multi-user threading lands separately.
        new_session = existing_session_id is None
        try:
            session_id = (
                await sessions.open(owner_key) if new_session else str(existing_session_id)
            )
        except Exception as exc:
            return _err(f"Failed to open browser session: {exc}", t0)

        transcript: list[dict[str, Any]] = []
        urls_visited: list[str] = []
        screenshot_paths: list[str] = []
        status = "running"
        final_summary = ""

        target_url = str(seed_url) if seed_url else None

        with BatchAuditLogger(
            services.audit_logger,
            event_type="browser_browse",
            actor=owner_key,
            target=url_path_only(target_url) if target_url else None,
            extra_details={"task_len": len(task), "allowed_domains": allowed_domains},
        ) as batch:
            try:
                # Allocate ONE page up-front and reuse it across every loop iteration —
                # passing None to get_page each time creates a fresh page and quickly
                # exhausts max_concurrent_pages_per_session.
                try:
                    _, page, page_handle = await sessions.get_page(session_id, None)
                except Exception as exc:
                    if new_session:
                        with contextlib.suppress(Exception):
                            await sessions.close(session_id)
                    return _err(f"Failed to allocate browser page: {exc}", t0)

                # If seed URL given, navigate first.
                if target_url:
                    await runtime.acquire_domain_slot(target_url)
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=_DEFAULT_NAV_TIMEOUT_MS)
                    await runtime.record_navigation()
                    urls_visited.append(url_path_only(page.url))
                    batch.add_step({"action": "navigate", "url": url_path_only(target_url), "seed": True})

                # Track last navigated URL so we can re-navigate after a recovery.
                last_navigated_url = url_path_only(page.url) if target_url else None

                prev_progress_sig: str | None = None
                no_progress_streak = 0

                for step_idx in range(max_steps):
                    # Re-fetch by handle (cheap dict lookup, no new page).
                    try:
                        _, page, page_handle = await sessions.get_page(session_id, page_handle)
                    except Exception as exc:
                        # Session may have been purged by runtime recycle. Recover by opening a fresh session.
                        from stackowl.infra.resilience import looks_like_dead_handle
                        from stackowl.tools.browser.sessions import (
                            BrowserSessionNotFoundError,
                        )

                        if not (isinstance(exc, BrowserSessionNotFoundError) or looks_like_dead_handle(exc)):
                            status = "error"
                            final_summary = f"page handle lost: {exc}"
                            break
                        log.tool.warning(
                            "browser_browse: session purged after recycle — re-opening",
                            exc_info=exc,
                            extra={"_fields": {"step": step_idx}},
                        )
                        try:
                            await runtime.ensure_available()
                            session_id = await sessions.open(owner_key)
                            _, page, page_handle = await sessions.get_page(session_id, None)
                            if last_navigated_url:
                                await page.goto(
                                    last_navigated_url, wait_until="domcontentloaded",
                                    timeout=_DEFAULT_NAV_TIMEOUT_MS,
                                )
                                await runtime.record_navigation()
                        except Exception as recover_exc:
                            status = "error"
                            final_summary = f"recovery failed: {truncate_for_error(str(recover_exc))}"
                            break
                    if page is None:
                        status = "error"
                        final_summary = "lost browser page handle"
                        break

                    # Captcha hard stop.
                    try:
                        captcha = await detect_captcha(page)
                    except Exception as exc:
                        log.tool.warning(
                            "browser_browse: detect_captcha failed — assuming no captcha",
                            exc_info=exc,
                        )
                        captcha = None
                    if captcha:
                        status = "captcha"
                        final_summary = f"captcha detected ({captcha}) at {url_path_only(page.url)}"
                        batch.add_step({"action": "captcha", "kind": captcha, "url": url_path_only(page.url)})
                        break

                    try:
                        elements = await index_dom_elements(page)
                        snippet = extract_markdown(await page.content(), include_links=False)
                        current_url = url_path_only(page.url)
                        current_title = await page.title()
                    except Exception as exc:
                        from stackowl.infra.resilience import looks_like_dead_handle

                        if looks_like_dead_handle(exc):
                            log.tool.warning(
                                "browser_browse: page died mid state-gather — will retry next step",
                                exc_info=exc, extra={"_fields": {"step": step_idx}},
                            )
                            # Force re-acquisition next iteration; runtime will self-heal via ensure_available.
                            page_handle = ""  # invalid handle → sessions.get_page raises → recovery path above
                            continue
                        # Non-dead error — bail cleanly.
                        status = "error"
                        final_summary = f"state gather failed: {truncate_for_error(str(exc))}"
                        break
                    state_text = _page_state_text(
                        current_url, current_title, elements, snippet,
                    )
                    last_navigated_url = current_url

                    messages = [
                        Message(role="system", content=_system_prompt(task, allowed_domains)),
                        Message(role="user", content=state_text),
                    ]
                    try:
                        result = await inner_provider.complete(messages, model="")
                    except Exception as exc:
                        log.tool.warning(
                            "browser_browse: inner LLM call failed",
                            exc_info=exc,
                            extra={"_fields": {"step": step_idx}},
                        )
                        status = "error"
                        final_summary = f"inner LLM call failed: {truncate_for_error(str(exc))}"
                        break

                    action = _extract_action_json(result.content)
                    if action is None:
                        batch.add_step({"step": step_idx, "raw_reply": truncate_for_error(result.content)})
                        log.tool.warning(
                            "browser_browse: could not parse action JSON — stopping",
                            extra={"_fields": {"step": step_idx, "reply_len": len(result.content)}},
                        )
                        status = "error"
                        final_summary = "inner agent returned unparseable action"
                        break

                    name = str(action.get("action", "")).lower()
                    batch.add_step({"step": step_idx, "action": action, "url": url_path_only(page.url)})

                    if name == "done":
                        status = "complete"
                        final_summary = str(action.get("summary", ""))
                        break
                    # No-progress guard: detect identical (page state, action) pairs.
                    prog_sig = f"{hash(state_text)}|{json.dumps(action, sort_keys=True, default=str)}"
                    if prog_sig == prev_progress_sig:
                        no_progress_streak += 1
                    else:
                        no_progress_streak = 0
                        prev_progress_sig = prog_sig
                    if no_progress_streak >= _NO_PROGRESS_LIMIT:
                        status = "no_progress"
                        final_summary = (
                            f"no progress: identical page state and action repeated at step {step_idx}"
                        )
                        log.tool.warning(
                            "browser_browse: no-progress guard tripped — breaking early",
                            extra={"_fields": {"step": step_idx, "action": name}},
                        )
                        break
                    if name == "navigate":
                        nav_url = str(action.get("url", ""))
                        if not is_domain_allowed(nav_url, allowed_domains):
                            log.tool.warning(
                                "browser_browse: domain violation",
                                extra={"_fields": {"url": url_path_only(nav_url)}},
                            )
                            status = "error"
                            final_summary = (
                                f"inner agent tried out-of-allowlist URL "
                                f"{url_path_only(nav_url)}"
                            )
                            break
                        await runtime.acquire_domain_slot(nav_url)
                        await page.goto(nav_url, wait_until="domcontentloaded", timeout=_DEFAULT_NAV_TIMEOUT_MS)
                        await runtime.record_navigation()
                        urls_visited.append(url_path_only(page.url))
                        last_navigated_url = url_path_only(page.url)
                    elif name == "click_index":
                        idx = int(action.get("index", -1))
                        target_el = next((e for e in elements if e.get("index") == idx), None)
                        if target_el is None:
                            log.tool.warning(
                                "browser_browse: click_index not found",
                                extra={"_fields": {"index": idx}},
                            )
                            continue
                        selector = self._selector_for(target_el)
                        if selector:
                            try:
                                await page.click(selector, timeout=10_000)
                            except Exception:
                                with contextlib.suppress(Exception):
                                    await page.get_by_text(target_el.get("text", "")).first.click(timeout=10_000)
                    elif name == "type_into_index":
                        idx = int(action.get("index", -1))
                        text = str(action.get("text", ""))
                        submit = bool(action.get("submit", False))
                        target_el = next((e for e in elements if e.get("index") == idx), None)
                        if target_el is None:
                            log.tool.warning(
                                "browser_browse: type_into_index not found",
                                extra={"_fields": {"index": idx}},
                            )
                            continue
                        selector = self._selector_for(target_el)
                        if selector:
                            with contextlib.suppress(Exception):
                                await page.fill(selector, text, timeout=10_000)
                                if submit:
                                    await page.press(selector, "Enter")
                                    await runtime.record_navigation()
                    elif name == "scroll":
                        direction = str(action.get("direction", "down"))
                        sign = -1 if direction == "up" else 1
                        with contextlib.suppress(Exception):
                            await page.evaluate(f"() => window.scrollBy(0, {sign} * window.innerHeight)")
                    elif name == "extract":
                        with contextlib.suppress(Exception):
                            extracted = extract_markdown(await page.content(), include_links=True)
                            batch.add_step({"step": step_idx, "extracted_chars": len(extracted)})
                    elif name == "screenshot":
                        out_dir = runtime.settings.screenshots_dir
                        out_dir.mkdir(parents=True, exist_ok=True)
                        path = out_dir / f"browse-{session_id[:8]}-{int(time.time() * 1000)}.png"
                        with contextlib.suppress(Exception):
                            await page.screenshot(path=str(path), full_page=False)
                            with contextlib.suppress(OSError):
                                path.chmod(0o600)
                            screenshot_paths.append(str(path))
                    elif name == "wait":
                        import asyncio
                        ms = int(action.get("ms", 1000))
                        await asyncio.sleep(ms / 1000.0)
                    else:
                        log.tool.warning("browser_browse: unknown action", extra={"_fields": {"action": name}})

                else:
                    status = "max_steps_reached"
                    final_summary = f"hit max_steps={max_steps} without 'done'"
            finally:
                if new_session:
                    with contextlib.suppress(Exception):
                        await sessions.close(session_id)

        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "browser_browse.execute: exit",
            extra={"_fields": {
                "status": status, "urls_visited": len(urls_visited),
                "steps": len(transcript), "duration_ms": duration_ms,
            }},
        )
        payload = {
            "status": status,
            "summary": final_summary,
            "urls_visited": urls_visited,
            "screenshot_paths": screenshot_paths,
            "step_count": len(transcript),
        }
        return ToolResult(
            success=status in ("complete", "max_steps_reached", "no_progress"),
            output=json.dumps(payload, default=str),
            error=None if status == "complete" else final_summary,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _selector_for(element: dict[str, Any]) -> str | None:
        """Build a CSS selector from an indexed element dict (best-effort)."""
        el_id = element.get("id")
        if el_id:
            return f"#{el_id}"
        name = element.get("name")
        if name:
            return f"[name=\"{name}\"]"
        href = element.get("href")
        if href:
            return f"a[href=\"{href}\"]"
        return None


def _err(msg: str, t0: float) -> ToolResult:
    return ToolResult(
        success=False, output="",
        error=msg, duration_ms=(time.monotonic() - t0) * 1000,
    )
