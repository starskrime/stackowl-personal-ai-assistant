# Browser backends

**Status.** Design, not built. Raised by Bakir 2026-08-26 — "I would like to have
solutions like their whole ideas" — after the browser measured 0 successes in 10
calls and ESC-54 made it the only permitted web path.

## Why this exists

The browser does not work on this box. Measured 2026-08-26: ten `browser_*` calls,
zero successes — five `TimeoutError`, four session-limit-on-recycle, one
`tool_error`. It is **not** a session leak: the TTL sweep runs, `close()` has real
callers, and the pool was never full. The engine keeps dying — 43 `browser process
gone`, 17 recycles with 17 matching `restart ok`. Self-healing catches every one
and restarts it. The process simply will not stay up.

That was survivable until today, because the model quietly fell back to
`web_fetch`. ESC-54 now makes the planner obey USER.md's permanent directive to
prefer the browser, and the task envelope refuses `web_fetch` as off-plan. So the
directive-compliant path is the only path, and it has a 0% success rate. Browser
breakage was promoted from a degradation to a hard stop for every web task.

**THE FINDING THAT REFRAMES IT.** The reference platform runs the same engine —
Camoufox, Firefox-based, the same anti-detection browser. Their tooling is not
better because the engine is better. Their own published capability table says:

| Backend | Dialog detect | Dialog respond | Frame tree | Cross-origin iframe eval |
|---|---|---|---|---|
| Local Chromium-family over CDP | yes | yes | yes | yes |
| Cloud provider | yes | yes | yes | yes |
| **Camoufox** | **no — REST only, no CDP** | **no** | partial | **no** |

We are running the one backend they document as least capable, as our only
backend. Everything good about their browser experience comes from the others.

## Model

Five ideas, in dependency order. The first is the one that matters; the rest are
cheap once it exists.

### 1. The backend is pluggable, and CDP is the contract

Their tools do not know which backend serves them. A backend returns a session
descriptor — `{session_id, session_name, cdp_url}` — and every tool works against
that. Local engine, a browser on another host, or a hosted service are the same
shape to the caller.

This is the whole unlock. **Once the contract is a CDP URL, the browser does not
have to run on this machine.** Engine stability on a Jetson stops being the
constraint, because the browser can run where it is stable.

### 2. Attach to a browser that is already running

`browser connect` finds a Chromium-family browser (Chrome, Chromium, Brave, Edge)
listening on a CDP port — default `9222` — and attaches. No launch, no profile
management, no install. The browser is someone else's problem.

For us the same mechanism points at a browser on the LAN. Bakir already runs a
second host; a Chromium with `--remote-debugging-port=9222` there is a backend
that does not die.

### 3. A persistent CDP supervisor

One connection per task, subscribed to `Page` / `Runtime` / `Target` events,
auto-attaching to every child target. It maintains a dialog queue and a frame
tree, and exposes them as a snapshot the tools read synchronously.

It closes two gaps we have today:

* **Native JS dialogs** (`alert` / `confirm` / `prompt` / `beforeunload`) block the
  page's JS thread. Without supervision the agent cannot know a dialog is open —
  the next tool call hangs or throws something opaque.
* **Cross-origin iframes are invisible** to top-level evaluation. The agent sees
  the iframe node in the snapshot and cannot click, type, or evaluate inside it.

### 4. Publish the capability matrix, and refuse honestly

Their table above is a deliverable, not a footnote. It states plainly that
Camoufox cannot do dialogs or cross-origin evaluation.

We have no such surface. A dialog-bearing page does not tell the agent "this
backend cannot answer dialogs" — it times out, and the timeout is
indistinguishable from a slow site. This is the same class as the four naming
defects found on 2026-08-26 (`freed for retry/re-arm`, `with the user's approval`,
`BrowserSessionLimitError` raised for a crash, two stub docstrings): the system
knows something and says something else.

### 5. What we already have — do not rebuild it

`browser_snapshot` already returns an accessibility tree with `[ref=eN]` handles,
which is the representation their design is built around. `BrowserSessionRegistry`
already does per-owner session isolation with TTL eviction, and the sweep is
started. `browser_vision` already does screenshot analysis. **The gap is backends
and CDP — not the tool surface.**

## Lifecycle

```
resolve backend (config)
  -> local engine        : launch Camoufox, as today
  -> attach              : connect to a CDP URL (localhost or LAN host)
  -> [future] remote pool: a backend that hands back a cdp_url

open session  -> {session_id, session_name, cdp_url}
supervise     -> if cdp_url is present, start a CDP supervisor for the task
tools         -> operate on session_id; supervisor state merges into snapshot
close         -> backend closes; TTL sweep reaps what leaks
```

## Invariants

1. **A tool never names a backend.** Every `browser_*` tool works against a
   session descriptor. Adding a backend must not touch a tool.
2. **No vendor logic in `src/`.** Backends are config-selected and
   interface-driven; hosted providers are not implemented here and their names do
   not appear in code (standing rule, and the self-hosted-only rule).
3. **A capability a backend lacks is REFUSED, not attempted.** If the active
   backend has no CDP surface, `browser_dialog` returns an honest refusal naming
   the reason. It never times out pretending to try.
4. **Falling back is visible.** If the browser is unavailable and the planner is
   permitted to use `web_fetch` instead, that substitution is stated in the answer
   — it contradicts a permanent user directive and the user must know.
5. **The local engine stays.** Attach is an addition, not a replacement. A box
   where Camoufox is fine keeps working with no config.

## Configuration

```yaml
browser:
  backend: local            # local | attach
  attach_url: ""            # e.g. http://192.168.1.81:9222  (backend: attach)
  supervisor: true          # start a CDP supervisor when the backend offers CDP
```

Everything ships enabled with `backend: local`, i.e. byte-identical to today.

## Observability

* `[browser] backend.resolve: exit` — which backend, and why, at INFO.
* `[browser] attach: connected` / `attach: unreachable` with the URL.
* `[browser] capability.refused` — tool, backend, missing capability. **INFO**, so
  it is countable; a DEBUG line here could never close an acceptance check.
* Keep the existing recycle counters — they are what proved the engine is dying.

## Failure modes

| Failure | Today | With this |
|---|---|---|
| Engine dies mid-session | recycle, purge, retry, 0% success | attach backend is unaffected |
| Attach URL unreachable | — | refuse at resolve, name the URL, fall back to local |
| Dialog opens | tool hangs, opaque timeout | refused honestly, or answered over CDP |
| Cross-origin iframe | silently unclickable | reachable via the child target |
| Browser down entirely | every web task hard-stops | degraded path stated in the answer |

## Verification

1. `browser.backend: attach` with a CDP URL on the LAN → `browser_navigate`
   succeeds while the local engine is stopped. **This is the acceptance check**;
   nothing else proves the constraint is lifted.
2. With `backend: local`, every existing browser test passes unchanged.
3. Point `attach_url` at a dead port → one honest refusal naming the URL, no
   timeout, no retry storm.
4. Re-run the 10-call measurement on the attach backend and compare the success
   rate against today's 0/10.

## Related

* ESC-54 — makes the browser the only permitted web path; the reason this is now
  urgent rather than annoying.
* `BrowserRuntimeRecycledError` (15b0391b) — the crash-vs-limit distinction that
  made this diagnosable.
* The tool-cap decision (2026-08-26) — raising the cap to 150 restored the browser
  sub-tools that were being dropped from machine lanes.

## Rules

* Port the DESIGN, never the code.
* Self-hosted only. "Run the browser elsewhere" means a host Bakir controls, not
  a hosted service.
* Measure before choosing an engine. Whether the crashes are Jetson-specific or
  would follow Camoufox anywhere is **not yet measured**, and switching engines on
  an assumption would be the same mistake as building from an unverified map
  verdict.

## Anti-patterns

* **Swapping Camoufox for Chromium locally and calling it fixed.** That is an
  engine guess. If the cause is memory pressure on this box, it follows.
* **Adding a hosted provider.** Violates the self-hosted rule and puts a vendor
  name in the tree.
* **Making `web_fetch` the silent fallback.** It reintroduces exactly the
  bot-blocking the user's directive exists to avoid, and hides it.
* **Building the supervisor first.** It is worthless against a backend with no CDP
  surface — which is the only backend we have today.

---
*Last verified: 2026-08-26 — measurements taken at commit 2619aa0c.*
