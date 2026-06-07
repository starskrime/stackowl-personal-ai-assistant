"""WebFetchTool — one-shot Camoufox-backed URL fetch + clean markdown extraction.

This is the convenience tool over the full browser surface — incognito
context, single navigation, Trafilatura-extracted markdown, context closed
on exit. For multi-step flows (click, type, screenshot) the LLM uses the
``browser_*`` atomic tools instead.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from stackowl.infra.net.ssrf_guard import SsrfGuard
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.browser._extraction import extract_links, extract_markdown
from stackowl.tools.browser._logging import url_path_only
from stackowl.tools.browser._retry import with_browser_retry

_MAX_OUTPUT_BYTES = 32_768  # markdown is denser than HTML; allow more than the old 8KB
_NAV_TIMEOUT_MS = 30_000
# Shared SSRF egress guard (E0-S2) — blocks private/loopback/link-local/metadata
# targets and non-http(s) schemes before any navigation.
_SSRF_GUARD = SsrfGuard()


async def _guard_navigation(route: Any) -> None:
    """Playwright route handler: re-validate every navigation/redirect hop.

    A pre-flight check on the initial URL is not enough — a public page can
    302 to ``http://169.254.169.254/`` and the browser would follow it. This
    aborts any navigation (including redirect targets) whose URL fails the SSRF
    policy; non-navigation subresources pass through. Fails closed on error.
    """
    request = route.request
    try:
        if request.is_navigation_request():
            ok, reason = _SSRF_GUARD.is_allowed(request.url)
            if not ok:
                log.tool.warning(
                    "web_fetch: blocked navigation/redirect by SSRF egress guard",
                    extra={"_fields": {"url": url_path_only(request.url), "reason": reason}},
                )
                await route.abort()
                return
    except Exception as exc:
        log.tool.warning("web_fetch: navigation guard error — aborting (fail closed)", exc_info=exc)
        with contextlib.suppress(Exception):
            await route.abort()
        return
    await route.continue_()


class WebFetchTool(Tool):
    """Fetch a URL with JS rendering via Camoufox; return clean markdown."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch the content of a URL with JavaScript rendering and stealth headers, "
            "returning clean markdown (default), plain text, or extracted links. "
            "Uses an ephemeral incognito browser context — no cookies or session state preserved."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
                "mode": {
                    "type": "string",
                    "enum": ["markdown", "text", "links"],
                    "default": "markdown",
                    "description": "Output format: markdown (default), plain text, or list of links.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        url = str(kwargs.get("url", ""))
        mode = str(kwargs.get("mode", "markdown"))
        log_url = url_path_only(url)
        log.tool.info("web_fetch.execute: entry", extra={"_fields": {"url": log_url, "mode": mode}})
        t0 = time.monotonic()

        if not url:
            return ToolResult(success=False, output="", error="Missing url parameter", duration_ms=0)

        # E0-S2 — SSRF egress guard: reject internal/metadata targets before navigating.
        ok, reason = _SSRF_GUARD.is_allowed(url)
        if not ok:
            log.tool.warning(
                "web_fetch.execute: blocked by SSRF egress guard",
                extra={"_fields": {"url": log_url, "reason": reason}},
            )
            return ToolResult(
                success=False, output="",
                error=f"URL blocked by egress policy: {reason}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        services = get_services()
        runtime = services.browser_runtime
        if runtime is None:
            log.tool.warning("web_fetch.execute: runtime not initialized")
            return ToolResult(
                success=False, output="",
                error="Browser runtime not initialized.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        async def _do() -> tuple[int, str]:
            await runtime.acquire_domain_slot(url)
            ctx_local: Any = await runtime.open_context(owner_key="local")
            page_local: Any = None
            try:
                # Re-validate every navigation/redirect against the SSRF policy.
                await ctx_local.route("**/*", _guard_navigation)
                page_local = await ctx_local.new_page()
                resp = await page_local.goto(
                    url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS,
                )
                await runtime.record_navigation()
                inner_status = resp.status if resp is not None else 0
                inner_html = await page_local.content()
                return inner_status, inner_html
            finally:
                if page_local is not None:
                    with contextlib.suppress(Exception):
                        await page_local.close()
                with contextlib.suppress(Exception):
                    await ctx_local.close()

        try:
            status, html = await with_browser_retry(_do, runtime, op_name="web_fetch")
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning(
                "web_fetch.execute: navigation failed",
                exc_info=exc,
                extra={"_fields": {"url": log_url, "duration_ms": duration_ms}},
            )
            return ToolResult(
                success=False, output="",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        if mode == "links":
            links = extract_links(html, base_url=url)
            output = "\n".join(f"- [{li['text'] or li['href']}]({li['href']})" for li in links)
        elif mode == "text":
            output = extract_markdown(html, include_links=False)
        else:
            output = extract_markdown(html, include_links=True)

        output = output[:_MAX_OUTPUT_BYTES]
        duration_ms = (time.monotonic() - t0) * 1000

        # Auto-stage as a low-confidence StagedFact for the memory promoter.
        await self._stage_in_memory(services, url, output, mode)

        log.tool.info(
            "web_fetch.execute: exit",
            extra={"_fields": {
                "url": log_url, "mode": mode, "status": status,
                "output_len": len(output), "duration_ms": duration_ms,
            }},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    async def _stage_in_memory(self, services: Any, url: str, content: str, mode: str) -> None:
        """Stage the fetched content as a low-confidence webpage fact. Best-effort, never raises."""
        bridge = services.memory_bridge
        runtime = services.browser_runtime
        if bridge is None or runtime is None or not runtime.settings.enable_memory_caching:
            return
        if not content.strip():
            return
        try:
            from stackowl.memory.models import StagedFact

            fact = StagedFact(
                content=content[:8000],
                source_type="webpage",
                source_ref=url_path_only(url),
                confidence=0.4,
                trust="untrusted",
            )
            await bridge.stage(fact)
        except Exception as exc:
            # Memory caching is opportunistic; never let it break a fetch.
            log.tool.debug(
                "web_fetch.execute: memory stage skipped",
                exc_info=exc,
                extra={"_fields": {"url": url_path_only(url), "mode": mode}},
            )
