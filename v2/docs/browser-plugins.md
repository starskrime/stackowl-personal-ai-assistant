# Building browser-derivative plugins

The browser runtime is exposed to plugins via the `browser_runtime` capability. Third-party tools (e.g., a `youtube_transcript` tool, a `linkedin_scraper`, a site-specific extractor) request it in `plugin.yaml` and receive a live `CamoufoxRuntime` + `BrowserSessionRegistry` through `PluginContext`.

## Manifest

```yaml
# plugin.yaml
name: my-youtube-transcript
version: 1.0.0
capabilities:
  - browser_runtime
  - memory_bridge
```

Without `browser_runtime` in the granted list, `PluginContext.browser_runtime` raises `PluginCapabilityDeniedError` on first access. Plugins follow the least-privilege convention — don't request the capability unless you need it.

## Tool skeleton

```python
from typing import Any

from stackowl.plugins.context import PluginContext
from stackowl.tools.base import Tool, ToolResult


class YoutubeTranscriptTool(Tool):
    def __init__(self, context: PluginContext) -> None:
        self._context = context

    @property
    def name(self) -> str:
        return "youtube_transcript"

    @property
    def description(self) -> str:
        return "Fetch the transcript of a YouTube video by URL."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        url = str(kwargs.get("url", ""))
        runtime = self._context.browser_runtime
        sessions = self._context.browser_sessions
        session_id = await sessions.open(owner_key="plugin:youtube_transcript")
        try:
            sess, page, _handle = await sessions.get_page(session_id, None)
            await runtime.acquire_domain_slot(url)
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await runtime.record_navigation()
            # ...click 'Show transcript', scrape into a string...
            transcript: str = await page.evaluate("/* your extraction JS */")
            return ToolResult(success=True, output=transcript, duration_ms=0.0)
        finally:
            await sessions.close(session_id)
```

## Best practices

1. **Use `runtime.acquire_domain_slot(url)` before every navigation.** The runtime's per-domain leaky-bucket rate-limit protects targets from runaway loops; bypassing it can get the whole StackOwl install banned.
2. **Call `runtime.record_navigation()` after every successful `goto()`.** This counts toward the recycle threshold; ignoring it can let the Firefox memory leak (#245) grow unbounded.
3. **Always close sessions in a `finally:` block.** Sessions count against `max_concurrent_sessions`. The TTL sweep is a backstop, not a primary cleanup.
4. **Don't log page content.** Use `from stackowl.tools.browser._logging import url_path_only, truncate_for_error`. These honor the project's URL-path-only + credential-scrub rules.
5. **Mark consequential tools.** Override `manifest` to set `action_severity="consequential"` for anything that submits forms, clicks buttons, runs JS, downloads files, or rotates credentials. The MCP exposure policy and audit log both consult this.
6. **Audit consequential calls.** If your plugin also declares `audit_logger`, call `audit_logger.append(event_type=f"plugin:{self.name}", actor=...)` after every consequential action.
7. **Honor `headless_mode` from the runtime's settings.** Plugins should never force `headless=False` — the runtime's policy already accounts for Xvfb availability on the host.

## Per-conversation state

Plugins do not see per-conversation `session_id`s today (the LLM tool loop is stateless from the tool's perspective). If your tool needs per-conversation isolation, derive your own `owner_key` from `plugin:<your-name>` and consider keying further by a token the LLM passes.

## Testing

Stub `AsyncCamoufox` at the import boundary:

```python
async def test_my_tool(monkeypatch):
    fake_browser = ...
    monkeypatch.setattr("camoufox.async_api.AsyncCamoufox", lambda **_: fake_browser)
    runtime = CamoufoxRuntime(BrowserSettings())
    await runtime.start()
    ...
```

`tests/tools/browser/test_sessions.py` is the canonical reference for the stubbing pattern.
