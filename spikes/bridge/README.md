# Bridge TLS/mDNS spike kit

**This is a throwaway kit.** It exists to prove, on real hardware, that a
per-install ephemeral CA plus a single `.local` host name can serve trusted
HTTPS on a home network — the Bridge's TLS/origin strategy (AD-13, AD-14).
It is not platform code: nothing under `spikes/bridge/` is imported by
`src/`, and its dependencies are declared inline in `kit.py` via a
[PEP 723](https://peps.python.org/pep-0723/) `# /// script` block. They are
**never** added to the repo's `pyproject.toml` or `uv.lock`.

## Run it

```bash
uv run spikes/bridge/kit.py start
```

`uv run` reads the dependency block at the top of `kit.py` and builds (and
caches) an isolated environment for this script alone — no project lockfile
is touched. This prints the new CA's SHA-256 fingerprint and guided trust
steps, advertises the install name over mDNS, and serves the kit's PWA at
`https://<install-name>:<port>/` until you press Ctrl-C.

Other subcommands:

```bash
uv run spikes/bridge/kit.py renew   # new CA + cert, prints the re-trust ceremony
uv run spikes/bridge/kit.py check   # the kit's own automated done-check (Chromium)
```

Both accept `--name <install-name>` and `--port <port>` (default `8443`).

## What "done" means for these stories

Real-device trust ceremonies (iOS, Android, desktop Chrome — by hand, on a
phone), live Telegram delivery, and real push-relay/microphone-hardware runs
are **Story 1.6's** job, not this kit's. Story 1.1 is done when
`uv run spikes/bridge/kit.py check` passes against Chromium **on the build
host**: it proves the certificate chain, the redirect, the subnet refusal,
and result writing, and writes `results/B1-build-host-chromium.json`.
Story 1.2 extends the same check with a CDP virtual authenticator that
proves setup-code refusal, passkey create/get, a copied-token replay
refusal, and matching-code device approval, writing
`results/B1-passkey-desktop-chrome-automated.json`. Story 1.3 extends it
again with the VAPID public key's availability/shape, endpoint refusal
(NFR29), real encrypted+signed delivery to the kit's own local push-service
stand-in (never a real relay), the service worker's push handling and
metadata-only cache scoping (FR25/FR26), notificationclick routing on and
off the home network, and microphone capture with permission persistence,
writing `results/B1-push-mic-desktop-chrome-automated.json`. Story 1.4
extends it again with the live stream carrier: a real aioquic WebTransport
connect (self-signed, `serverCertificateHashes`-pinned), automatic fallback
to `fetch`-streamed SSE, resume-after-a-dropped-connection with no
loss/duplicates (proven by comparing sent vs. received cursors), staleness
within one heartbeat timeout, two-tab leader hand-off with no cursor gap,
and a deliberately throttled client receiving `resync` while a healthy
client keeps advancing (proving the replayer/fan-out never blocks) — plus
connect time, resume-replay time and this process's own memory as P50/P95
across repeated samples, writing
`results/B2-carrier-desktop-chrome-automated.json`. Real-device WebTransport
interop (Safari, cellular, Jetson-class memory) is Story 1.6's job. Story 1.5
extends it once more with the committed `frontend/build/` Svelte/Three.js
build served at `/csp-check/`: the exact AD-36 header set (asserted on
success **and** error responses alike), the owl mark rendered as a DOM
`<img>` and inside a `WebGPURenderer` scene that auto-falls back to WebGL2 on
default headless Chromium, the kit's one named Trusted Types policy created
exactly once and genuinely exercised, zero real `securitypolicyviolation`
events, and a self-test proving that violation detector isn't vacuous,
writing `results/B5-desktop-chrome-automated.json`. Real-device browser runs
across every B1 browser class are Story 1.6's job.

## Layout

- `kit.py` — the single entrypoint (`start` / `renew` / `check`).
- `bridge_spike/ca.py` — ephemeral CA + one signed leaf cert; the CA private
  key is never written to disk and is dropped from memory the moment the
  leaf is signed.
- `bridge_spike/mdns.py` — advertises `<install-name>.local` via
  `avahi-publish-service` (Linux) or `dns-sd -R` (macOS); prints the exact
  command instead of crashing if the tool is missing.
- `bridge_spike/server.py` — the one HTTPS listener: IPv4-only, subnet
  allowlist, redirect-first middleware, the static PWA, the checklist
  results endpoint, and the passkey/device-key/device-approval routes.
  `SECURITY_HEADERS` (AD-36, verbatim) is stamped on **every** response by
  the outermost `_security_headers_guard` middleware, including error
  responses raised as `web.HTTPException` deeper in the stack (the subnet
  guard's 403, the redirect guard's 307); `check.py` imports the same
  constant rather than re-declaring it, so the enforced and asserted
  policies can never drift apart. Also mounts the committed
  `frontend/build/` output at `/csp-check/` (Story 1.5).
- `bridge_spike/setup_code.py` — the one-time setup code minted at kit start
  (Story 1.2); no route can mint another.
- `bridge_spike/webauthn_flow.py` — py_webauthn passkey registration/
  authentication (Story 1.2).
- `bridge_spike/tokens.py` — bearer-token issuance and signed-request
  (method/path/timestamp/nonce/body-hash) verification (Story 1.2).
- `bridge_spike/device_requests.py` — the single-pending second-device
  name+matching-code approval state machine (Story 1.2).
- `bridge_spike/telegram_bot.py` — the kit's own *test* Telegram bot, with
  the collision guard against the platform's `telegram_channel.bot_token`
  (Story 1.2).
- `bridge_spike/push.py` — VAPID keypair generation, the NFR29 endpoint
  validator (https-only, refuses loopback/private/link-local — the SSRF
  guard AD-19 calls for), the push-subscription store, the metadata-only
  payload builder, and a `pywebpush`-based sender (Story 1.3).
- `bridge_spike/push_stub.py` — a local push-service stand-in: its own
  ephemeral HTTPS listener (IP-SAN leaf cert, bound to `127.0.0.1`) that
  acks a Web Push POST the way a real relay would, for `check.py` to prove
  `push.py`'s sender against. Never a real relay (FCM/APNs/Mozilla), same
  principle as `telegram_bot.py`'s test bot (Story 1.3).
- `bridge_spike/webtransport_cert.py` — short-lived (≤14-day), self-signed
  ECDSA P-256 certificates for the WebTransport listener, with a
  current+next `WebTransportCertStore` kept in memory only and rotated with
  overlap (AD-14, Story 1.4).
- `bridge_spike/webtransport_server.py` — the aioquic WebTransport listener:
  binds the identical port NUMBER as the HTTPS TCP site, over UDP only
  (AD-13). `Origin` checked before auth, a signed auth message enforced
  within 5s/4KB or the session is closed, 0-RTT never wired up at all (no
  `session_ticket_fetcher`/`session_ticket_handler` ever passed to
  `serve()`), QUIC address validation (`retry=True`) and a connection cap
  (Story 1.4, AD-38, NFR25).
- `bridge_spike/stream.py` — the one cursor-numbered synthetic event source
  both carriers stream from (`StreamHub`, `stream_for_client`): replayed at
  recorded rates including a deliberate burst, a heartbeat carrying
  `head_cursor` at an announced interval, and a bounded per-client queue
  that resyncs instead of blocking the replayer on overflow (Story 1.4,
  AD-31, NFR11/13/46). No real recorded-platform-event journal exists yet
  to pull from — see the story's own spec Design Notes.
- `bridge_spike/check.py` — the automated Chromium check shared by
  `kit.py check` and `tests/test_automated_check.py`.
- `frontend/` — a standalone, pinned-stack (Svelte 5.57.0, `three` 0.186.0,
  Vite 8.3.0, TypeScript 6.0.3/svelte-check 4.7.6, Node ≥22.12.0)
  Vite+Svelte+TypeScript project proving the Bridge's real front-end stack
  (AD-20) holds under AD-36's strict CSP/Trusted Types policy (Story 1.5).
  Its own `package.json`/`package-lock.json` pin every version exactly and
  never enter the platform's `pyproject.toml`/`uv.lock`, mirroring this
  kit's own PEP 723 isolation. `src/App.svelte` renders the AD-40 owl mark
  (`logo/stackowl-mark.svg`, never hand-redrawn) both as a DOM `<img>` and,
  via `src/scene.ts`, inside a `three/webgpu` `WebGPURenderer` scene
  (`SVGLoader` fetches the same SVG file at runtime) that auto-falls back to
  WebGL2. `src/trusted-types-policy.ts` registers the kit's one named
  Trusted Types policy, genuinely exercised by the renderer-backend status
  label and by patching `SVGLoader`'s own `DOMParser.parseFromString` call.
  `npm run build` outputs to `build/` (never `dist/` — root `.gitignore`
  ignores that repo-wide), which **is committed** so a fresh clone needs
  neither Node nor a network to serve it; `npm run lint` runs
  `scripts/verify-lint.mjs`, which fails on `{@html}`/`.innerHTML =`/
  `.insertAdjacentHTML(` both in the real `src/` (zero errors required) and
  against the three `fixtures/` files (each MUST fail, proving the ban still
  fires); `npm run check` runs `svelte-check`. A scoped
  `frontend/.gitignore` ignores only `node_modules/`.
- `bridge_spike/static/` — the PWA (`index.html`, `app.js`,
  `manifest.webmanifest`, `sw.js`, `offline-summary.html`, icons). Styling
  and scripting are entirely external (`styles.css`, `index-bootstrap.js`,
  `offline-summary.js`) — the enforced `style-src`/`script-src 'self'` has
  no `unsafe-inline` — and no page uses `innerHTML`/`insertAdjacentHTML`
  (Story 1.5). `app.js`
  exposes its passkey/device-key/device-approval, push
  (`window.BridgePush`), microphone (`window.BridgeMic`), and live-stream
  (`window.BridgeStream`) functions so `check.py` can drive them directly
  through a real browser. `window.BridgeStream` connects WebTransport-first
  with automatic SSE fallback, elects one leader tab per browser via Web
  Locks + `BroadcastChannel` (AD-31), tracks the resume cursor in
  `localStorage`, and exposes `simulateDrop()`/`forceFallback()`/
  `releaseLeadership()` test seams (no CDP surface exists for WebTransport,
  mirroring the WebAuthn/push precedent below). `sw.js` handles
  `push` (metadata-only cache, FR25/FR26) and `notificationclick` (opens the
  item at home, else the cached summary — never an error page); it also
  exposes a `message`-driven test seam calling the same click-decision
  function, since no browser automation surface can simulate a real OS
  notification click.
- `tests/` — see the test file per module above, plus `test_ca.py`,
  `test_server.py`, `test_server_auth_routes.py`, `test_server_push_routes.py`,
  `test_server_stream_routes.py`, `test_server_security_headers.py`,
  `test_stream.py`, `test_webtransport_cert.py`,
  `test_webtransport_server.py`,
  `test_push.py`, `test_push_stub.py`, `test_automated_check.py`,
  `test_mdns.py`, `test_kit_cli_output.py`.
- `results/` — checklist + check output, gitignored (`.gitkeep` keeps the
  directory itself versioned).

## Story 1.2's own test Telegram bot (never the platform's)

Set these two env vars to exercise the setup-code/device-request Telegram
delivery for real (Story 1.6's job) — the kit refuses to start if
`BRIDGE_SPIKE_TEST_TELEGRAM_BOT_TOKEN` equals the platform's resolved
`telegram_channel.bot_token`:

```bash
export BRIDGE_SPIKE_TEST_TELEGRAM_BOT_TOKEN=<a SEPARATE bot's token, from @BotFather>
export BRIDGE_SPIKE_TEST_TELEGRAM_USER_IDS=<your Telegram user id>   # exactly one to auto-send the setup code
uv run spikes/bridge/kit.py start
```

Neither variable is read by, or shared with, the platform's own Telegram
channel config.

## Tests

```bash
# All kit unit tests (no Playwright needed):
uv run --with cryptography --with aiohttp --with webauthn --with python-telegram-bot --with pywebpush --with http-ece --with requests --with aioquic python -m pytest spikes/bridge/tests

# The full automated done-check, including the Playwright/Chromium test,
# the CDP virtual-authenticator passkey/device-approval ceremonies, the
# push/microphone proofs, the WebTransport/SSE live-stream proofs, and the
# CSP/Trusted-Types/frontend-build proofs:
uv run spikes/bridge/kit.py check

# The frontend/ project's own checks (Story 1.5) — lint fails on a
# forbidden-pattern fixture and passes on real source, svelte-check
# type-checks, and the build must match the committed build/ with no drift:
cd spikes/bridge/frontend && npm ci && npm run lint && npm run check && npm run build
```

## Never

- Touch the platform's own TLS/secret-store code — this kit is fully
  self-contained.
- Add a WireGuard/tunnel path — that approach (AD-15) is retired.
- Relax the redirect or subnet checks "to make a demo easier".
- Commit generated CA/server private keys or `results/*.json`.
- Import or touch `src/stackowl/authz/` or any platform identity code — it
  doesn't exist yet (Epic 5's job).
- Call the real Telegram Bot API from this kit's own test suite — Telegram
  allows only one poller per bot, and a real call could knock the live
  platform bot offline. Tests stub the `Application`/`Bot` layer instead.
- Call a real push relay (FCM/APNs/Mozilla) from this kit or its own test
  suite. `push.py`'s sender is proven for real (VAPID signing, `aes128gcm`
  encryption) only against `push_stub.py`'s local stand-in — real-device
  delivery against a real relay is Story 1.6's job.
- Exercise a real WebTransport peer beyond localhost under test (Safari/iOS,
  cellular, a real network drop). `check.py`'s WebTransport/SSE proofs run
  entirely against the kit's own aioquic listener on `127.0.0.1` through a
  real headless Chromium; every other multi-device/network interop run is
  Story 1.6's job (`awaiting-operator`).
- Relax, shrink, or make conditional any part of `SECURITY_HEADERS` (AD-36)
  to get a page working — fix the offending markup/script instead. Story
  1.5's own gate (B5) is explicit: the failing construct is removed from the
  build; the policy itself is never the thing that gives.
- Add a real-device (non-Chromium, non-build-host) browser run for Story
  1.5 — that is Story 1.6's job.
- Enter `frontend/`'s dependencies into the platform's own
  `pyproject.toml`/`uv.lock`, or build/fetch `frontend/build/` at kit-run
  time — the committed output must serve from a fresh clone with neither
  Node nor a network.
