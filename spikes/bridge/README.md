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
writing `results/B1-push-mic-desktop-chrome-automated.json`.

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
- `bridge_spike/check.py` — the automated Chromium check shared by
  `kit.py check` and `tests/test_automated_check.py`.
- `bridge_spike/static/` — the PWA (`index.html`, `app.js`,
  `manifest.webmanifest`, `sw.js`, `offline-summary.html`, icons). `app.js`
  exposes its passkey/device-key/device-approval, push
  (`window.BridgePush`), and microphone (`window.BridgeMic`) functions so
  `check.py` can drive them directly through a real browser. `sw.js` handles
  `push` (metadata-only cache, FR25/FR26) and `notificationclick` (opens the
  item at home, else the cached summary — never an error page); it also
  exposes a `message`-driven test seam calling the same click-decision
  function, since no browser automation surface can simulate a real OS
  notification click.
- `tests/` — see the test file per module above, plus `test_ca.py`,
  `test_server.py`, `test_server_auth_routes.py`, `test_server_push_routes.py`,
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
uv run --with cryptography --with aiohttp --with webauthn --with python-telegram-bot --with pywebpush --with http-ece --with requests python -m pytest spikes/bridge/tests

# The full automated done-check, including the Playwright/Chromium test,
# the CDP virtual-authenticator passkey/device-approval ceremonies, and the
# push/microphone proofs:
uv run spikes/bridge/kit.py check
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
