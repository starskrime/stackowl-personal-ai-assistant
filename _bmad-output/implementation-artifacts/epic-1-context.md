# Epic 1 Context: Proof on your own devices

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Before any real Bridge code is built, prove — on the owner's own iPhone, Android and desktop, on the home network — that every mechanism the Bridge architecture depends on actually works: private-CA trust, passkey sign-in and device approval, push notifications and microphone access, a live WebTransport stream with automatic SSE fallback, and the strict CSP/Trusted Types policy under the pinned front-end stack. Each story builds part of one throwaway test kit (`spikes/bridge/`, never entering the platform lockfile); every device run is recorded as a pass/fail result. The epic ends by turning every result into a verdict per gate (B1, the carrier half of B2, B5) with its binding fail branch, so later epics build on measured evidence from real devices rather than assumptions. The kit is then deleted from `main` (preserved only on an unpushed local branch).

## Stories

- Story 1.1: Trust the Bridge's own certificate on your phone at home
- Story 1.2: Sign in with a passkey on the local name and approve a second device
- Story 1.3: Push, microphone and the away-from-home summary on your devices
- Story 1.4: A live stream over WebTransport with automatic SSE fallback, on your phone
- Story 1.5: The strict security policy holds on every browser
- Story 1.6: Verdicts decide what gets built

## Requirements & Constraints

- The kit runs from a fresh clone on the platform's own host on the home network, reached by the owner's stock devices; each story's automated check must pass against Chromium on the build host before any real-device run is owed.
- Real-device runs (stock iPhone iOS 26.4+ and iOS 27, Android Chrome 148+, desktop Chrome) are captured only in Story 1.6 and parked as `awaiting-operator`; a device class with no result file is reported as "not run", never as a pass.
- **Gate B1** (private-CA trust, passkeys on the local name, WebTransport certificate hashes, setup code, second-device approval, microphone, service worker, push subscription, away-from-home summary, PWA install, 7-day Safari idle, renewal ceremony) — fail branch: the owner decides the path given the failing step and device class, with no preset fallback.
- **Gate B2, carrier half only** (WebTransport interop vs. SSE fallback, cursor resume with a burst and backgrounding, leader-tab hand-off, slow-client resync) — fail branch: that carrier becomes SSE-only until a maintained WebTransport stack is chosen; a client class showing silent gaps uses SSE + POST.
- **Gate B5** (committed build runs under the exact CSP/Trusted Types policy on every B1 browser) — fail branch: the failing construct is removed from the build; the policy itself is never relaxed to reach a pass.
- Every result file (`spikes/bridge/results/B1-*.json`, `B2-carrier-*.json`, `B5-*.json`) records kit version, browser/OS version and a timestamp; the verdict command scores these against the spine's pass criteria and writes `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md`.

## Technical Decisions

- **Origin & TLS (AD-13, AD-14):** one `.local` install host name, advertised via the host's own mDNS responder, is the only origin (IPv4, home-network subnets only, no plain HTTP). An ephemeral per-install CA signs one ECDSA P-256 server cert (≤825 days validity) then is destroyed — no CA key exists after setup. WebTransport uses separate short-lived (≤14 days) self-signed ECDSA P-256 certs identified by `serverCertificateHashes`, delivered only over authenticated HTTPS.
- **Identity (AD-16, AD-17):** sign-in creates a non-extractable WebCrypto ECDSA P-256 device key; every request/stream carries a bearer token plus a signature over method, path, timestamp, server nonce and body hash. Passkeys use py_webauthn with a fixed RP ID (the install name), exact expected origin, `userVerification=required`. There is exactly one owner principal; web and voice handles resolve to it in code.
- **Device approval (AD-37):** a setup code is issued only at boot/host CLI, accepted only from the home network, and a new device is approved by matching a device name + short code shown on both the requesting device and an already signed-in device or Telegram — never minted by a network request.
- **Notifier (AD-19):** Web Push is standard VAPID with an encrypted, metadata-only payload (item id, kind, intensity, public rendering only); a push endpoint must be `https` and is refused if it resolves to loopback/private/link-local. Away-from-home notification taps show a cached metadata-only summary from the service worker, never an error page and never cached content.
- **Carriers (AD-9, AD-11, AD-12):** WebTransport over HTTP/3 is primary, with automatic fallback to `fetch`-streamed SSE (never `EventSource`, never a credential in a URL) on the same origin when WebTransport is unavailable or its handshake fails after one hash refresh. 0-RTT is refused, `Origin` is checked before auth, and a late/oversized signed auth message closes the session. A server heartbeat (interval announced at hello) drives liveness/staleness and bounds token revocation to one interval.
- **Client store (AD-31):** one client store per browser; multiple tabs share one stream via Web Locks leader election plus `BroadcastChannel`, with hand-off on leader close.
- **Front end (AD-20, AD-40):** Svelte + TypeScript 6 (type-checked by svelte-check) + Vite; the owl mark is imported only from `logo/stackowl-mark.svg`; Three.js `three/webgpu` (`WebGPURenderer`) auto-falls back to WebGL2.
- **CSP (AD-36):** every response carries the exact header set — strict CSP with `require-trusted-types-for 'script'`, `nosniff`, `no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and a `Permissions-Policy` granting microphone to self only; exactly one named Trusted Types policy; `{@html}`/`innerHTML`/`insertAdjacentHTML` are lint failures.
- **Downloads (AD-25):** every runtime download (assets, weights, engines) is pinned by immutable revision URL and SHA-256, verified before an atomic rename.
- The kit is throwaway by design: it declares its own dependencies inline (never entering the platform lockfile), and its Telegram steps must use a separate test bot token — never the live platform's bot token, since Telegram allows only one update consumer per bot.

## UX & Interaction Patterns

- Device approval is anti-phishing by construction: the requesting device and the approving surface (a Bridge screen or a Telegram message) show the *same* device name and short matching code; approval is a signed tap or an explicit Telegram approval, and only one device request can be pending at a time.
- At home, tapping a Web Push notification opens the relevant item directly; away from home (Bridge unreachable), the tap instead shows a cached, metadata-only summary with an "open at home" affordance and a link to continue in Telegram — never an error page.
- A request by IP address or any Host other than the install name must redirect to the install name and must never show a passkey prompt on that origin.

## Cross-Story Dependencies

- Story 1.2 (passkeys, sign-in) depends on the HTTPS/local-name origin proven in Story 1.1.
- Story 1.3 (push, microphone, away-from-home summary) depends on a signed-in device from Story 1.2.
- Story 1.4 (WebTransport/SSE stream) runs on the same HTTPS origin established in Story 1.1 and, on iOS idle-persistence checks, benefits from Story 1.2's token/session model.
- Story 1.5 (CSP/front-end build) is independent of 1.1–1.4 but its result feeds the same verdict process.
- Story 1.6 aggregates the result files from Stories 1.1–1.5 (B1, the carrier half of B2, B5) into the epic's verdicts and is the only story where the kit is deleted from `main`; every later epic depends on its verdicts rather than on assumptions.
