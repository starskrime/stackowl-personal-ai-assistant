---
title: 'Story 1.3: Push, microphone and the away-from-home summary on your devices'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: 'b2bfa05cef5afab61ebe7503854d8f04634ad8b5'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      AD-19 says a push-subscription row "is deleted on revocation," but no
      device-revocation/unenroll route exists anywhere in the kit.
    evidence: |-
      Confirmed by reading server.py's full route list: no revoke/unenroll
      endpoint exists for any resource type (passkey device, bearer token, or
      push subscription). This is a pre-existing gap in the device/token
      lifecycle dating back to Story 1.2 (which introduced signed-in devices
      but never a revoke path), not something Story 1.3 introduced or
      worsened, and it is outside this story's captured intent (the epics.md
      AC list for Story 1.3 never asks for revocation).
    location: >-
      spikes/bridge/bridge_spike/server.py, spikes/bridge/bridge_spike/push.py
    severity: low
---

<intent-contract>

## Intent

**Problem:** Story 1.1 proved TLS/mDNS trust and Story 1.2 proved passkey sign-in and a device-bound signed bearer token, but nothing yet proves VAPID Web Push delivery, push-endpoint validation, the away-from-home cached summary, or real microphone capture actually work -- AD-19, FR25, FR26, NFR29 are unverified.

**Approach:** Extend the same throwaway kit with a VAPID push-subscription store and `pywebpush`-based sender, a local push-service stand-in (never a real relay) driven by `check.py`, a service worker `push`/`notificationclick` handler that shows a metadata-only cached summary when off the home network, and a browser mic-capture page with a live level meter. Done when the kit's automated Chromium check proves payload encryption/signing, endpoint refusal, the offline summary page's cache-scoping, and mic permission grant/persistence; real-device runs remain Story 1.6's job.

## Boundaries & Constraints

**Always:** push subscription endpoint validated before storage (https only, refuse loopback/private/link-local -- NFR29); push payload carries only item id, kind, intensity and a short public rendering, never full content (FR26); `/api/push/subscribe` and `/api/push/unsubscribe` go through `_require_signed_request` (server.py:330), signed via `app.js`'s existing `signedFetch` helper, requiring an already-signed-in device (Story 1.2 dependency); VAPID keypair generated fresh per kit run, never committed; service worker caches only the metadata payload for the offline summary, proven by an automated check that inspects cache contents (FR25); `pywebpush` (platform-allowed per ARCHITECTURE-SPINE.md AD-19) declared only in `kit.py`'s PEP 723 inline block, mirroring the existing version-pin convention, never touching root `pyproject.toml`/`uv.lock`; mic capture uses `getUserMedia` + a Web Audio level meter, with permission-persistence checked across a close/reopen cycle in the automated check; the local push-service stand-in runs on its own ephemeral port with its own start/stop lifecycle inside `check.py`'s `run_check()`.

**Never:** call a real push relay (FCM/APNs/Mozilla) from the kit or its tests -- local stand-in only, same principle as Story 1.2's Telegram stub; import or touch `src/stackowl/authz/` or `src/stackowl/notifications/` (platform code, read-only reference only); change Story 1.1/1.2 behavior (CA/mDNS/cert, passkey/token/device-approval code) beyond additive route/module additions; require a real phone or a real push relay for this story's own automated-check completion bar (that's Story 1.6's); add CSP/Permissions-Policy middleware (Story 1.5's scope) unless the automated check actually fails without it; commit `spikes/bridge/results/*`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid subscribe | signed-in device POSTs a valid `https` push endpoint | subscription stored; VAPID public key already available for `PushManager.subscribe` | n/a |
| Non-https endpoint | POST with `http://` endpoint | refused (NFR29) | 400 |
| Loopback/private endpoint | POST with endpoint resolving to loopback/private/link-local | refused (NFR29) | 400 |
| Push delivery | kit sends a notification via the local stand-in | SW `push` event fires; `showNotification` shown with only metadata (id/kind/intensity/rendering) | n/a |
| Tap on home network | `notificationclick`, kit reachable | opens that item's page directly | n/a |
| Tap off home network | `notificationclick`, kit unreachable | SW shows cached metadata-only summary + "open at home" + Telegram link, no error page | automated check proves cache holds nothing beyond that metadata |
| Mic grant | owner grants mic permission | live level meter shows captured audio | n/a |
| Mic permission persistence | page closed and reopened | checklist records whether the permission persisted | n/a |

</intent-contract>

## Code Map

- `spikes/bridge/bridge_spike/server.py:177-213` -- `ServerConfig`/`BridgeServer.__init__`: existing stateful stores (`self.tokens`, `self.device_requests`, line ~205); add `self.push_subscriptions` alongside them.
- `spikes/bridge/bridge_spike/server.py:330` -- `_require_signed_request`: the Story 1.2 signed-request decorator, called first thing in `_handle_device_register_key`/`_handle_approve_device_request` (lines 425, 448, 468) -- reuse verbatim to gate the new push routes.
- `spikes/bridge/bridge_spike/server.py:213,215` -- `self.app = web.Application(middlewares=[...])` + `_install_routes()`: existing subnet/redirect middlewares apply to any new route for free; append push routes here.
- `spikes/bridge/bridge_spike/server.py:398-422` -- `_handle_device_register_key`: template for "validate the payload fully, then store" ordering -- the push-subscribe handler should validate the endpoint URL before persisting a subscription.
- `spikes/bridge/kit.py:1-13` -- PEP 723 inline dependency block: add `pywebpush` here, mirroring the existing version-pin convention (`pyproject.toml:8-15` ranges: floor = current major tested, ceiling = next major excluded).
- `spikes/bridge/kit.py:122-144` -- `_start_test_telegram_bot`: the precedent for "an external test double wired in only when configured, never the real service" -- a local push-service stand-in follows this same shape.
- `spikes/bridge/kit.py:147-166` -- `_serve()`: generate the VAPID keypair between `ServerConfig(...)` construction (line 165) and `BridgeServer(config)` (line 166), mirroring how CA setup happens earlier in the same function.
- `spikes/bridge/bridge_spike/check.py:260,266,280,296,321` -- `run_check()`: add a `_check_push_and_mic(...)` step alongside `_check_passkey_and_device_approval` (called at line 280), writing a new `B1-push-mic-desktop-chrome-automated.json` via `_write_result` (line 321), matching the existing two-result-file split.
- `spikes/bridge/bridge_spike/check.py:74` -- `_free_port()`: reuse to bind the local push-service stand-in's own ephemeral port, with lifecycle inside `run_check()`'s `try`/`finally`.
- `spikes/bridge/bridge_spike/check.py:131-158` -- `_new_context_with_virtual_authenticator`: the CDP-session pattern for WebAuthn faking. For mic, no CDP equivalent exists yet in this kit -- use Playwright's `context.grant_permissions(["microphone"], origin=...)` (first use in this codebase) plus launch args `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream` alongside the existing `--ignore-certificate-errors-spki-list` args.
- `spikes/bridge/bridge_spike/check.py:161-257` -- `_check_passkey_and_device_approval`: the worked example of driving `window.BridgeAuth.*` via `page.evaluate` and asserting on the returned JSON -- mirror this for new `window.BridgePush`/`window.BridgeMic` helpers.
- `spikes/bridge/bridge_spike/static/sw.js` (17 lines today) -- currently a bare PWA shell: `install`/`activate` + a network-first `fetch` handler, no `push`/`notificationclick` listeners and nothing ever cached. Add both listeners plus a `caches.open`/`cache.put` step that stores only the metadata payload.
- `spikes/bridge/bridge_spike/static/app.js:133-160` -- `signedFetch(method, path, bodyObj)`: every authenticated call goes through this; the push-subscribe/unsubscribe POSTs must too.
- `spikes/bridge/bridge_spike/static/app.js:326-339` -- `window.BridgeAuth = {...}`: the exposure pattern `check.py` drives via `page.evaluate` -- add `window.BridgePush` and `window.BridgeMic` the same way.
- `spikes/bridge/bridge_spike/static/index.html:77-124` -- existing section/button/result-div pattern to copy for new "Push notifications" and "Microphone" UI sections; the `#checklist-form` (`#device_class`/`#passed`/`#note`) is reused as-is.
- `spikes/bridge/tests/test_server_auth_routes.py:1-9,~30-40` -- HTTP-level `TestClient`/`_make_server()` pattern (plain HTTP, no TLS/Playwright) -- mirror for a new `test_server_push_routes.py`.
- `spikes/bridge/tests/test_telegram_bot.py:1-5` -- docstring states the "never call the real external service" rule explicitly for Telegram; the same rule applies to push (stub/local stand-in only).
- `spikes/bridge/tests/test_automated_check.py:27-45,60-65` -- per-step assertion enumeration pattern to extend with the new push/mic step keys and the new result file's shape.
- `_bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-09-12/ARCHITECTURE-SPINE.md:256` -- AD-19 source text for AC1/AC2, and names `pywebpush` as the allowed VAPID library.
- `spikes/bridge/README.md:32-42,95-104,106-117` -- "what done means" section to extend for this story; the documented unit-test command's `--with` list needs `--with pywebpush`; the "Never" section needs a push-relay bullet next to the existing Telegram one.
- No CSP/Permissions-Policy middleware exists in `server.py` yet (AD-36 belongs to Story 1.5) -- note only; build it here only if mic capture actually fails without it in Chromium.

## Tasks & Acceptance

**Execution:**
- `spikes/bridge/bridge_spike/push.py` -- new: VAPID keypair generation (per kit run, in-memory), a subscription store (endpoint/keys/device binding), an endpoint validator (https-only, refuse loopback/private/link-local via `ipaddress`/resolution), and a `pywebpush`-based sender building the metadata-only payload -- AC1/AC2/FR26/NFR29.
- `spikes/bridge/bridge_spike/push_stub.py` -- new: a local push-service stand-in HTTP server (own ephemeral port) that acks a push POST the way a real relay would, for `check.py` to point subscriptions at -- AC6, "never a real relay".
- `spikes/bridge/bridge_spike/server.py` -- add `self.push_subscriptions` store; routes `GET /api/push/vapid-public-key` (unauthenticated), `POST /api/push/subscribe` and `POST /api/push/unsubscribe` (behind `_require_signed_request`), validate-then-store ordering.
- `spikes/bridge/kit.py` -- `_serve()` generates the VAPID keypair and passes it into `ServerConfig`, mirroring the CA setup step; PEP 723 block gains `pywebpush`.
- `spikes/bridge/bridge_spike/static/sw.js` -- `push` event handler (parse payload, `showNotification` with only metadata), `notificationclick` handler (open the item on the home network; else show the cached metadata-only summary + "open at home" + Telegram link, no error page), and a cache step storing only the metadata payload.
- `spikes/bridge/bridge_spike/static/app.js` -- `window.BridgePush` (subscribe/unsubscribe via `signedFetch`, `PushManager.subscribe` with the VAPID public key) and `window.BridgeMic` (`getUserMedia`, a Web Audio level meter, a permission-persistence check) exposed for `check.py`.
- `spikes/bridge/bridge_spike/static/index.html` -- new "Push notifications" and "Microphone" sections mirroring the existing button/result-div pattern.
- `spikes/bridge/bridge_spike/check.py` -- `_check_push_and_mic(...)`: starts the push stand-in, subscribes, sends a push, asserts SW cache scoping (metadata only) via `page.evaluate` reading `caches`, asserts endpoint refusal for non-https/loopback inputs, grants mic permission with fake-device launch args + `grant_permissions`, drives `window.BridgeMic`, writes `B1-push-mic-desktop-chrome-automated.json`.
- `spikes/bridge/tests/test_push.py`, `test_push_stub.py` -- unit tests for the new modules, including the endpoint-refusal edge cases from the I/O matrix.
- `spikes/bridge/tests/test_server_push_routes.py` -- HTTP-level route tests via `TestClient`, mirroring `test_server_auth_routes.py`.
- `spikes/bridge/tests/test_automated_check.py` -- extend with assertions on the new push/mic step keys and the new result file.
- `spikes/bridge/README.md` -- document the new modules, extend "what done means", add `--with pywebpush` to the documented unit-test command, add a push-relay bullet to "Never".

**Acceptance Criteria:**
- Given a signed-in device, when it subscribes and the kit sends a push, then delivery uses standard VAPID with an encrypted payload carrying only item id, kind, intensity and a short public rendering.
- Given a push subscription request whose endpoint is not `https` or resolves to loopback/private/link-local, then it is refused.
- Given a delivered notification, when tapped on the home network, then the kit opens that item's page.
- Given a delivered notification tapped off the home network, then the service worker shows a cached metadata-only summary with "open at home" and a Telegram link, no error page, and an automated check proves the cache holds nothing beyond that metadata.
- Given the kit's microphone page in a browser tab and in the installed app, when the owner grants microphone access, then a live level meter shows captured audio and the checklist records whether the permission persists after closing and reopening.
- Given a local push-service stand-in, when the automated check runs, then it covers payload encryption/signing, endpoint refusals, and the offline summary page in Chromium.

## Spec Change Log

## Review Triage Log

### 2026-09-16 — Review pass
- verdicts: 19 findings — high 0, medium 1, low 15, false 3, maybe-false 0
- findings:
  - `[low]` `[reject]` (blind-hunter) `validate_endpoint` (NFR29) only runs at subscribe time, so a hostname endpoint that resolves public at subscribe and later re-resolves (DNS rebinding) to loopback/private/link-local bypasses the SSRF guard at send time — real, but unlikely for a throwaway single-operator home-network kit whose only real trust boundary is the subnet gate (same disposition as Story 1.2's own rejected "no rate-limiting on device-requests" finding), and a real fix (pin the resolved address or re-validate at send) is more than a direct correction.
  - `[false]` `[reject]` (blind-hunter) claimed `steps["push_delivered_encrypted_and_signed"]` could read `True` without the vapid/encryption assertions ever running if `push_stub.received` is empty despite a successful `send_push` — refuted: `push_stub.py`'s `_handle_push` appends to `self.received` *before* returning its HTTP response, and `send_push`'s underlying synchronous `requests` call only returns once that response is received, so `send_ok` becoming `True` structurally guarantees `push_stub.received` is already non-empty at that point.
  - `[low]` `[patch]` (blind-hunter) `detail["push_delivered_encrypted_and_signed"]` is never set when `stored` is empty (subscribe failed) or `push_stub.received` is empty, unlike every other step in `check.py` which records detail unconditionally — confirmed by reading `check.py:431-484`. Action: a fallback detail string is written whenever the crypto-detail branch is skipped, naming which precondition (no subscription / no received push) was missing.
  - `[low]` `[patch]` (blind-hunter) `sw.js`'s `push` listener silently discards a JSON-parse failure (`catch (err) { payload = {}; }`) with no logging, unlike the sibling `handlePushMetadata` catch a few lines below which does `console.warn` — confirmed by reading the file. Action: added a matching `console.warn` in the `push` listener's catch.
  - `[low]` `[patch]` (blind-hunter) `app.js`'s `permissionState()` swallows any `navigator.permissions.query` failure into `"unknown"` with no logging, hiding the real error from anyone debugging a failed mic-permission-persistence run — confirmed by reading the function. Action: added `console.warn` in the catch.
  - `[low]` `[reject]` (blind-hunter) `PushSubscriptionStore.add()` lets one signed-in device register unlimited distinct endpoints with no cap — unlikely to matter for a throwaway single-operator home-network kit (same disposition as Story 1.2's own rejected "no rate-limiting on device-requests" finding), and a real fix (a cap + eviction policy) is more than a direct correction.
  - `[low]` `[defer]` (blind-hunter) AD-19 says "a subscription row belongs to its device session and is deleted on revocation," but no device-revocation/unenroll route exists anywhere in `server.py` — real gap, but pre-existing across the whole device/token model since Story 1.2 (no revoke endpoint for any resource type exists yet), not introduced or worsened by this story, and out of this story's captured intent (the epics.md AC list for Story 1.3 never asks for revocation).
  - `[low]` `[patch]` (blind-hunter) `check.py`, `tests/test_push.py`, and `tests/test_push_stub.py` import `http_ece` and `requests` directly, but neither is declared in `kit.py`'s PEP 723 block or the README's `--with` list — they currently resolve only because `pywebpush` happens to depend on both transitively; confirmed by grepping the PEP 723 block and the README's documented pytest command. Action: added explicit pinned `http_ece`/`requests` entries to `kit.py`'s PEP 723 block and to the README's documented unit-test `--with` list.
  - `[low]` `[reject]` (blind-hunter) stale/expired push subscriptions (a real relay's 404/410) are never handled — `push_stub.py` always acks 201 — out of scope: the story's captured intent (epics.md's stated AC list) never asks for relay-side expiry/cleanup handling, and the spec's own Design Notes explicitly hand all real-relay operational behavior to Story 1.6.
  - `[low]` `[patch]` (blind-hunter) `registration_id` (captured from the `ServiceWorker.workerRegistrationUpdated` CDP event) is passed straight into `ServiceWorker.deliverPushMessage` with no assertion that it was actually populated before the `activated` event fires — same root cause as edge-case-hunter's row below. Action: added an explicit check that raises a clear, named error if `registration_id` is still `None` before the CDP call, instead of letting a stale race surface as an opaque low-level protocol error.
  - `[low]` `[patch]` (edge-case-hunter) `validate_endpoint` silently accepts an endpoint when a custom `resolve()` callback returns an empty address list (the `for addr in candidates` loop never executes, so nothing is ever refused) — confirmed by reading `push.py:86-95`; not reachable via the real `_default_resolve` (which always raises or returns ≥1 address) but a real gap for any future custom resolver. Action: `validate_endpoint` now raises `EndpointRefused` when `candidates` is empty.
  - `[low]` `[patch]` (edge-case-hunter) same root cause as blind-hunter's `registration_id` row above — grouped, same action.
  - `[false]` `[reject]` (edge-case-hunter) claimed an exception from `page.evaluate` between `context.route(...)` and `context.unroute(...)` in `_check_push_and_mic` would leave the nonce route aborted, "corrupting the subsequent home-network check and offline-summary load" — refuted: no exception handler wraps that call in `_check_push_and_mic`; an exception there propagates out of the whole function uncaught and aborts `run_check` entirely (the same "loud failure on an unexpected infrastructure problem is acceptable signal" design Story 1.2's own review explicitly accepted), so the claimed silent corruption of later steps cannot happen — the run fails visibly instead.
  - `[low]` `[patch]` (edge-case-hunter) a push with an unparseable/malformed payload (`metadata.id` undefined) is cached under the literal key `/__push-summary__/undefined`, clobbering any other id-less push's cached entry — confirmed by reading `narrowToMetadata`/`cacheMetadataOnly`/`pushSummaryCacheKey`. Action: `handlePushMetadata` now skips caching (and skips showing a notification) when `metadata.id` is undefined.
  - `[low]` `[patch]` (edge-case-hunter) `app.js`'s `simulateNotificationClick` awaits a service-worker reply message with no timeout — a future regression that stops the SW's `message` listener from replying would hang the automated check indefinitely — confirmed by reading the function. Action: added a timeout that resolves `{ok: false, error: "timeout"}` instead of hanging forever.
  - `[low]` `[reject]` (edge-case-hunter) an uncaught `getUserMedia`/`AudioContext` exception in `window.BridgeMic.captureLevel` would abort `run_check` entirely instead of recording a failed mic step — same accepted "loud failure on an unexpected infrastructure problem is acceptable signal, not a masked failure" design Story 1.2's own review explicitly considered and accepted for an analogous case; extending that already-reviewed tradeoff to one more call site is not a new defect.
  - `[low]` `[patch]` (edge-case-hunter) `sw.js`'s `install` listener now blocks `self.skipWaiting()` on `cache.addAll` succeeding, where the prior code (removed by this diff) called `skipWaiting()` unconditionally under an explicit "no offline caching strategy needed beyond don't crash" design comment — confirmed by reading the diff. Action: `cache.addAll` failures are now caught (`.catch(() => {})`) before chaining `skipWaiting()`, preserving the original "install never fails" behavior.
  - `[false]` `[reject]` (edge-case-hunter) claimed the away-from-home proof silently overclaims coverage of the real `clients.openWindow()` navigation path — refuted: `sw.js`'s own comment directly above `decideNotificationClickTarget` explicitly documents that `openWindow()`'s real side effect is deliberately not exercised by the automated check (it requires a genuine user-gesture event a test seam cannot produce) and explains why — a disclosed, intentional test-design choice, not a hidden or silently overclaimed proof.
  - `[medium]` `[patch]` `[pre-verified]` (verification-gap) `_default_resolve` — the resolver actually used by the real `POST /api/push/subscribe` route whenever an endpoint is a hostname rather than an IP literal — has no test coverage of its `OSError → EndpointRefused` conversion or its IPv6 zone-suffix stripping; every existing hostname-resolution test in `test_push.py` substitutes an explicit fake `resolve`, and every route-level test uses only IP literals, so none of them ever call `socket.getaddrinfo`. Graded medium since AC2's literal wording ("resolves to loopback/private/link-local") does not distinguish IP-literal from hostname endpoints, and hostname endpoints are exactly the shape Story 1.6's real-relay runs will use. Action: added `test_push.py` cases that monkeypatch `socket.getaddrinfo` directly (not the `resolve` parameter) to cover both a resolution failure and a scoped IPv6 loopback result.

## Design Notes

Real-device push delivery (an actual FCM/APNs push to a physical phone) and real microphone hardware capture on a physical device remain out of scope for "done" here, mirroring Stories 1.1/1.2's identical scoping: the epic's "How this epic runs" section makes the automated Chromium check this story's completion bar, with every real-device run owed by Story 1.6 (parked `awaiting-operator` there). Push delivery is proven end-to-end against a local stand-in, never a real relay. Nothing in this story requires an action only a human can perform outside the repo, so it dispatches through the normal ready-for-dev pipeline, not `awaiting-operator`.

## Verification

**Commands:**
- `uv run spikes/bridge/kit.py check` -- expected: automated Chromium check passes, writes the new push/mic result file alongside the existing two.
- `uv run --with cryptography --with aiohttp --with webauthn --with python-telegram-bot --with pywebpush python -m pytest spikes/bridge/tests` -- expected: all kit unit tests pass, including the new push/mic modules.

## Auto Run Result

**Summary:** Implemented Story 1.3 end-to-end, extending the Bridge spike kit (`spikes/bridge/`) from Stories 1.1/1.2 with a VAPID push-subscription store and `pywebpush`-based sender, an NFR29 SSRF-guarding endpoint validator, a local push-service stand-in (never a real relay), a service worker `push`/`notificationclick` handler with a metadata-only cache for the away-from-home summary (FR25/FR26), an offline-summary page, and browser microphone capture with a live level meter and permission-persistence checking — all proven by a real CDP-driven Chromium run against real server code, not mocks. A full review pass (blind-hunter, edge-case-hunter, verification-gap, intent-alignment) found 19 findings; 3 were false, 4 were rejected low-severity/out-of-proportion, 1 was deferred as a pre-existing gap, and 10 (across 9 grouped root causes) were real and patched — 1 medium, 9 low, none high.

**Files changed:**
- `spikes/bridge/bridge_spike/push.py` -- new: VAPID keypair generation, the NFR29 endpoint validator (https-only, refuses loopback/private/link-local on every resolved address; now also refuses when a custom resolver returns zero addresses -- review patch), the subscription store, the metadata-only payload builder, and a `pywebpush`-based sender.
- `spikes/bridge/bridge_spike/push_stub.py` -- new: a local push-service stand-in -- its own ephemeral HTTPS listener (IP-SAN leaf cert, bound to `127.0.0.1`) that acks a Web Push POST the way a real relay would, for `check.py` to prove `push.py`'s sender against. Never a real relay.
- `spikes/bridge/bridge_spike/static/offline-summary.html` -- new: the away-from-home cached summary page, reading the same Cache Storage entry the service worker wrote.
- `spikes/bridge/bridge_spike/ca.py` -- additive `ip_sans` parameter to `setup()`/`_sign_leaf_cert()` (default `None`, identical to every existing caller's behavior) so `push_stub.py`'s IP-literal-dialed leaf cert can carry an `iPAddress` SAN.
- `spikes/bridge/bridge_spike/server.py` -- extended with the `push_subscriptions` store and the VAPID public-key/subscribe/unsubscribe routes behind the existing signed-request gate and subnet/redirect middlewares.
- `spikes/bridge/kit.py` -- `_serve()` generates the VAPID keypair alongside the CA; PEP 723 block gains `pywebpush`, and (review patch) explicit pinned `http-ece`/`requests` entries for the two libraries `check.py`/tests import directly but were previously only resolving transitively through `pywebpush`.
- `spikes/bridge/bridge_spike/static/sw.js` -- `push` and `notificationclick` listeners, a metadata-only cache (FR25/FR26 enforced structurally by `narrowToMetadata`), and a `message`-driven test seam for `notificationclick` (no browser automation surface exists for a real OS notification click). Review patches: a malformed payload with no `id` is no longer cached under the literal `/__push-summary__/undefined` key; a push JSON-parse failure is now logged; `install`'s `cache.addAll` failure no longer blocks `skipWaiting()`, restoring the prior "install never fails" behavior.
- `spikes/bridge/bridge_spike/static/app.js` -- `window.BridgePush` (subscribe/unsubscribe/VAPID key/notificationclick test seam) and `window.BridgeMic` (capture + permission state) exposed for `check.py`. Review patches: `permissionState()` now logs query failures instead of silently reporting `"unknown"`; `simulateNotificationClick` now times out after 5s instead of hanging forever if the service worker never replies.
- `spikes/bridge/bridge_spike/static/index.html` -- new "Push notifications" and "Microphone" sections (subscribe button, level meter).
- `spikes/bridge/bridge_spike/check.py` -- new `_check_push_and_mic()`: VAPID key shape, endpoint refusals (unpatched), real encrypted+signed delivery to the local stand-in decrypted back to prove metadata-only content, SW push handling via CDP `ServiceWorker.deliverPushMessage`, cache scoping, notificationclick on/off the home network via real network interception, and microphone capture (fake-device Chromium flags) with permission persistence across a close/reopen; writes `results/B1-push-mic-desktop-chrome-automated.json`. Review patches: a missing `detail` entry on the crypto-verification failure path now always records why; `registration_id` is now asserted non-`None` before the CDP `deliverPushMessage` call instead of surfacing an opaque protocol error.
- `spikes/bridge/README.md` -- documents the new modules, extends "what done means", adds `--with pywebpush --with http-ece --with requests` to the documented unit-test command, adds a push-relay bullet to "Never".
- `spikes/bridge/tests/test_push.py`, `test_push_stub.py`, `test_server_push_routes.py` -- new unit/HTTP-level test files, including the review's added coverage of the real `_default_resolve` path (`socket.getaddrinfo` failure and IPv6 zone-suffix stripping).
- `spikes/bridge/tests/test_automated_check.py` -- extended with assertions on every new push/mic step key and the new result file's shape.

**Review findings breakdown (19 total: high 0, medium 1, low 15, false 3, maybe-false 0):**
- **Patched (10 findings across 9 grouped entries, 1 medium, 9 low):** missing `detail` on the push-delivery crypto-verification failure path; a silently-discarded push JSON-parse failure with no logging; `permissionState()` swallowing errors into `"unknown"` with no logging; `http_ece`/`requests` imported directly but undeclared (only resolving transitively via `pywebpush`); an unasserted `registration_id` possibly `None` before a CDP call (2 findings, same root cause); `validate_endpoint` silently accepting an endpoint when a custom resolver returns zero addresses; a malformed push payload's `undefined` id clobbering another cached summary; `simulateNotificationClick` hanging forever with no timeout; the service worker's `install` listener now able to block `skipWaiting()` on a precache failure, regressing the prior "install never fails" design; and (verification-gap, pre-verified, graded medium as literal-AC-adjacent) the real `_default_resolve` hostname-resolution path having no test coverage at all.
- **Deferred (1):** no device-revocation/unenroll route exists anywhere in the kit for any resource type, contradicting AD-19's "deleted on revocation" -- a pre-existing gap in the device/token model since Story 1.2, not introduced by this story and outside its captured intent.
- **Rejected -- false (3):** a claimed race where the push-delivery step could read `True` without its crypto assertions running (refuted: the local stand-in's handler appends to `received` before returning its HTTP response, so a successful `send_push` structurally guarantees `received` is non-empty); a claimed silent corruption of later notificationclick checks from an unhandled exception mid-route-interception (refuted: no handler catches that exception, so it aborts the whole check loudly instead); a claimed silent overclaim of the `clients.openWindow()` navigation path (refuted: `sw.js`'s own comment explicitly documents and explains that exact, deliberate limitation).
- **Rejected -- low, unlikely + fix adds complexity (4):** the SSRF guard not being re-validated at send time (DNS rebinding) for a throwaway single-operator home-network kit whose only real trust boundary is the subnet gate; no cap on push subscriptions per device; no stale/expired-subscription (404/410) handling, explicitly out of this story's captured intent and owed to Story 1.6's real-relay work; an uncaught mic-capture exception aborting the check instead of recording a failed step, the same accepted "loud failure is acceptable signal" design Story 1.2's own review explicitly considered and accepted for an analogous case.

**Follow-up review recommendation:** `false`. No patched entry was `high`, and only one (`medium`) was patched -- the convergence rule requires two or more `medium` patches (or any `high`) on a first pass, so this does not meet that bar.

**Verification performed:**
- `uv run --with cryptography --with aiohttp --with webauthn --with python-telegram-bot --with pywebpush --with http-ece --with requests python -m pytest spikes/bridge/tests -q` -- 155 passed (up from 153 pre-review, the 2 new resolver-path tests included).
- `uv run spikes/bridge/kit.py check` -- PASS, all 22 steps true (the 10 pre-existing 1.1/1.2 steps plus 12 new Story 1.3 steps: device sign-in, VAPID key shape, endpoint refusals, subscribe-to-stand-in, encrypted+signed delivery, payload-decrypts-to-metadata-only, delivered-to-service-worker, cache-holds-metadata-only, notificationclick on/off the home network, offline-summary rendering, mic capture, mic permission persistence); run twice for stability, both PASS; all three result files written.
- `git status`/`git diff` -- confirmed the change is scoped to `spikes/bridge/` plus this spec file; `pyproject.toml`/`uv.lock`, `src/stackowl/`, and Stories 1.1/1.2's CA/mDNS/cert/passkey/token/device-approval code are untouched except for `ca.py`'s one additive, backward-compatible `ip_sans` parameter; `spikes/bridge/results/*` stays gitignored.

**Residual risks:**
- The four rejected-low findings and the one deferred finding remain theoretically possible (send-time SSRF re-validation, subscription-count cap, stale-subscription cleanup, a mic-capture exception aborting the check, and no device-revocation route) but are judged out of proportion to a throwaway, single-operator, home-LAN spike kit, or out of this story's captured intent -- none blocks this story's acceptance criteria.
- The CDP `ServiceWorker.deliverPushMessage` mechanism and the fake-media-stream/`grant_permissions` combination are Chromium-version-sensitive; verified against the currently cached Chromium build only.
- Real-device push delivery (an actual FCM/APNs push to a physical phone) and real microphone hardware capture remain explicitly out of scope for this story's "done" bar, owed to Story 1.6, per the epic's own scoping (mirrored from Stories 1.1/1.2's identical Design Notes disposition).
