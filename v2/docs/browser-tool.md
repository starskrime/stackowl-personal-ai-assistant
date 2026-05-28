# Browser Tools (Camoufox)

StackOwl v2 ships a stealth browser surface backed by [Camoufox](https://github.com/daijro/camoufox), a Firefox-150 fork that spoofs fingerprints at the C++ level. The browser is auto-installed on first boot; you don't need to run any setup command.

## Tool surface

The LLM sees **one convenience tool**, **17 atomic tools**, and **one meta-tool**:

| Tool | Severity | What it does |
|---|---|---|
| `web_fetch` | read | One-shot grab + clean markdown via Trafilatura |
| `browser_navigate` | read | Open page; returns `{session_id, page_handle, title, status, captcha_detected}` |
| `browser_extract` | read | Pull content (markdown / text / links / html) |
| `browser_screenshot` | read | PNG saved under `~/.stackowl/screenshots/` (chmod 0600) |
| `browser_wait_for` | read | Wait for a CSS selector |
| `browser_recall_url` | read | "Have I seen this URL before?" via memory bridge |
| `browser_tab_open / list / close` | write | Multi-tab control |
| `browser_click` | write | CSS selector or visible text |
| `browser_type` | write | Fill input, optional submit |
| `browser_scroll` | write | down / up / top / bottom |
| `browser_cookies_get / set / clear` | write | Cookie jar ops |
| `browser_eval_js` | **consequential** | Arbitrary JS in the page (script hash audited) |
| `browser_upload` | **consequential** | `<input type=file>` set |
| `browser_download` | **consequential** | Capture file download with size guard + SHA256 |
| `browser_browse` | **consequential** | Inner-LLM agent drives a multi-step task with `allowed_domains` hard-enforced |
| `browser_close` | write | Release a session |

Consequential tools are flagged with `!` in `/permissions` output and are denied by default over MCP (see [MCP exposure](#mcp-exposure)).

## Sessions and profiles

A *session* wraps one Playwright `BrowserContext` plus a dict of `Page` handles. The LLM gets a `session_id` from `browser_navigate` (or `browser_browse`) and threads it through subsequent calls in the same conversation turn.

**Per-owner isolation.** CLI gets `owner_key="local"`, Telegram gets `owner_key=f"telegram:{chat_id}"`. Profiles live under `~/.stackowl/browser-profiles/<owner_key>/<profile_name>/` (chmod 0700). Telegram user A's `gmail` profile is at a different path from user B's `gmail` profile — no cookie leakage.

**Persistent profiles.** Pass `profile_name="my-github"` to `browser_navigate` and the resulting context survives restart. First use logs an INFO line announcing where cookies are stored.

**TTL eviction.** Sessions idle longer than `BrowserSettings.session_idle_timeout_minutes` (default 30) are evicted by a background sweep. Hard cap of 8 concurrent sessions per process (configurable).

## Slash commands

- `/tools` — list every registered tool with its action severity glyph
- `/browser sessions` — list active sessions for this conversation
- `/browser close <id-prefix>` / `/browser close all` — release sessions
- `/browser settings` — show current `BrowserSettings`
- `/browser fetch-binary` — re-run `python -m camoufox fetch` (rarely needed)
- `/browser profile list` / `/browser profile delete <name>` — profile lifecycle

## Background jobs

Six scheduler handlers ship with the browser surface (registered when the runtime starts):

- `website_watch` — poll a URL, hash content, emit a `website_changed` event on diff
- `screenshot_archive` — daily/weekly screenshots into `~/.stackowl/workspace/knowledge/screenshots/YYYY-MM-DD/`
- `browser_recycle` — hourly belt-and-suspenders against the Firefox memory leak (issue #245); also evicts idle sessions
- `browser_cache_eviction` — daily prune of `~/.stackowl/cache/browser/` (>7d) and screenshots (>30d)
- `credential_rotation` — session liveness check for persistent profiles (detects login-page redirects, warns on expiry)
- `profile_backup` — weekly tar of every profile dir into `~/.stackowl/backups/browser-profiles/`

Schedule any of these via your config or by asking an owl to "watch X" / "screenshot Y daily".

## Performance

Defaults tuned for the Jetson Orin (8 GB) target:

- `headless_mode = "virtual"` (Xvfb-backed) — strictly stealthier than `headless=True`; auto-degrades to `"true"` when Xvfb is missing
- `block_images = True` — 2-4× speedup
- `block_webrtc = True` — prevents IP leak when using proxies
- `geoip = True` — auto-aligns timezone / locale / WebRTC to the proxy's exit IP
- `humanize = True` — adds human-like cursor delays
- `nav_recycle_threshold = 200` navigations OR `idle_recycle_minutes = 30` → restart the browser process
- `max_concurrent_sessions = 8`, `max_concurrent_pages_per_session = 4` → ~32-page ceiling

`/health` reports cold-start time and process RSS via the `BrowserContributor`.

## Memory integration

Every `web_fetch` / `browser_extract` (markdown mode) auto-stages a `StagedFact(source_type="webpage", confidence=0.4, source_ref=<url-path>)`. The existing `fact_promoter` / `fact_reinforcer` flow decides whether to promote. Re-visiting a URL bumps `reinforcement_count` — a free "have I seen this before?" signal.

Use `browser_recall_url` to query: `{found, content, last_seen_at, reinforcement_count}`.

Screenshots auto-stage as `StagedFact(source_type="screenshot")` (opt-in for vision captions via `BrowserSettings.enable_screenshot_captions`).

## Anti-bot

- Stealth fingerprints handled at the Camoufox binary level (C++, not JS injection — much harder to detect than playwright-stealth)
- Captcha detection probes for Cloudflare Turnstile, hCaptcha, reCAPTCHA, DataDome, PerimeterX, Arkose Funcaptcha; `browser_navigate` returns `captcha_detected=<kind>` and `browser_browse` halts with `status="captcha"`
- Per-domain leaky-bucket rate-limiter (default 2 seconds between navigations to the same host)
- `browser_browse` hard-enforces `allowed_domains` — out-of-allowlist navigation aborts mid-step with an audit event

## MCP exposure

The MCP server (when enabled) auto-exposes the read-severity browser tools (`web_fetch`, `browser_navigate`, `browser_extract`, `browser_screenshot`, `browser_recall_url`, `browser_cookies_get`, `browser_tab_list`, `browser_wait_for`). The consequential set is denied by default. To allow external MCP clients (e.g., Claude Desktop) to issue clicks, downloads, JS evals, or the inner browse meta-tool:

```yaml
mcp_server:
  allow_browser_writes: true
```

The server advertises a `browser` capability so MCP clients can detect support before calling.

## Audit

Every consequential browser action emits one audit row (SHA-256 chained):

```
browser_eval_js     actor=scout    target=https://x.com/path   details={script_sha256_prefix, script_len, session_id}
browser_download    actor=scout    target=https://x.com/file   details={bytes, sha256, stored_path, session_id}
browser_browse      actor=local    target=https://x.com        details={steps[], step_count, exception, task_len}
```

`browser_browse` uses `BatchAuditLogger` to commit ONE row per browse invocation with a `steps[]` array — not N chained rows.

## Owl defaults

Built-in personas (registered automatically when the runtime starts):

| Owl | Browser allowlist |
|---|---|
| `secretary` | `web_fetch`, `browser_extract`, `browser_recall_url` |
| `scout` | Full atomic set + `browser_browse` |
| `librarian` | `web_fetch`, `browser_extract`, `browser_screenshot`, `browser_recall_url` |
| `archivist` | `web_fetch`, `browser_screenshot`, `browser_recall_url` |
| `demo-owl` | none |

User-configured owls in `stackowl.yaml` take precedence over built-ins.

## Plugins

Third-party tools can declare the `browser_runtime` capability:

```yaml
# plugin.yaml
name: my-youtube-transcript
capabilities:
  - browser_runtime
  - memory_bridge
```

`PluginContext.browser_runtime` then exposes the live `CamoufoxRuntime`; `PluginContext.browser_sessions` exposes the registry. Both raise `PluginCapabilityDeniedError` if the capability isn't granted.

## Operator install (one-time, optional)

Camoufox auto-fetches its binary on first boot. To run the recommended `headless="virtual"` mode on a fresh Jetson / Linux server, install the system deps once:

```bash
sudo apt install -y libgtk-3-0 libx11-xcb1 libasound2 xvfb
```

Without these, the runtime auto-degrades to `headless="true"` (slightly more detectable but functional).
