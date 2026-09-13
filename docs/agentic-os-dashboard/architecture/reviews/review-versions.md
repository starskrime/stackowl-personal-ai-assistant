# Review — versions and reality check

- **Artifact:** `ARCHITECTURE-SPINE.md` (updated 2026-09-13)
- **Lens:** was every committed decision checked against a live source (web, the existing project, or the current starter) rather than asserted from training data?
- **Reviewer date:** 2026-09-13
- **Method:** direct registry queries (PyPI JSON, npm registry), the local `pyproject.toml` and `uv.lock`, raw MDN browser-compat-data, Chromium and WebKit source at `main`, the Node.js release schedule, WebKit release notes, and the run's `.memlog.md`.

## Verdict

The version pins are real and current: every one of them exists on its registry as of today. The weak spots are platform capabilities the spine treats as settled.

- **The biggest miss:** Chromium's QUIC stack refuses certificates from a per-install CA. On every Chromium browser, WebTransport as specified will always fall back to SSE.
- **Five high items:** SSE bearer-token auth, TypeScript 7 tooling, the WebAuthn hostname rule, aioquic's draft support, and the WireGuard "no router changes" promise. Each changes a rule, not just a number.

| Tier | Count |
| --- | --- |
| Critical | 1 |
| High | 5 |
| Medium | 13 |
| Low (confirmed or minor) | 16 |

---

## Critical

### C1 — WebTransport on Chromium rejects the per-install CA (AD-11, AD-13, AD-14)

- **Spine says:** WebTransport over HTTP/3 is the primary carrier. The same certificate, issued by a per-install private CA, serves TCP and UDP. SSE is only the fallback, "when the browser lacks WebTransport or UDP is blocked".
- **Current source says:** Chromium `main` still runs a "known root" check on QUIC.
  - A WebTransport session opened without `serverCertificateHashes` is verified with unknown roots disallowed, unless the `origins_to_force_quic_on` or `force_quic_everywhere` flags or WebTransport developer mode are set.
  - Otherwise the result is `ERR_QUIC_CERT_ROOT_NOT_KNOWN`: "The certificate presented on a QUIC connection does not chain to a known root and the origin connected to is not on a list of domains where unknown roots are allowed."
  - A user-installed per-install CA is never a known root. Chrome desktop, Chrome Android, Edge and Samsung Internet will therefore fail the WebTransport handshake on every connection. The browser does have WebTransport and UDP is not blocked, so the spine's fallback condition does not describe this case.
  - Safari does not do this. WebKit evaluates normal server trust and also honours `serverCertificateHashes`.
- **Sources:**
  - https://chromium.googlesource.com/chromium/src/+/main/net/quic/crypto/proof_verifier_chromium.cc (lines ~428–432, `!is_issued_by_known_root && !ShouldAllowUnknownRootForHost` → `ERR_QUIC_CERT_ROOT_NOT_KNOWN`)
  - https://chromium.googlesource.com/chromium/src/+/main/net/quic/dedicated_web_transport_http3_client.cc (`CreateProofVerifier`: the unknown-root allowance is granted only by force-QUIC flags or `webtransport_developer_mode`; with fingerprints it uses `kCustomCertificateMaxValidityDays`)
  - https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h (`QUIC_CERT_ROOT_NOT_KNOWN, -380`)
  - https://github.com/WebKit/WebKit/blob/main/Source/WebKit/NetworkProcess/webtransport/cocoa/NetworkTransportSessionCocoa.mm (`leafCertificateMatchesWebTransportHash`, `SecTrustEvaluateAsyncWithError`)
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/WebTransport.json (`serverCertificateHashes`: Chrome 100, Firefox 125, Safari 26.4)
- **Not in the memlog:** its WebTransport entry checked browser support and cookie behaviour only. It still lists Safari `serverCertificateHashes` as "unconfirmed"; that is now confirmed.
- **Fix:** amend AD-11 and AD-14 before epics. Pick one of:
  1. **Recommended:** open WebTransport with `serverCertificateHashes`.
     - The gateway issues a dedicated WebTransport leaf certificate: ECDSA P-256, validity of 14 days or less, which fits AD-14's short-lived certificates.
     - It publishes the SHA-256 hash in the authenticated HTTPS server hello or snapshot. The client passes that hash to the WebTransport constructor.
     - Rotation must overlap, and the hello must carry both hashes during the change.
     - Supported in Chrome 100+, Firefox 125+ and Safari 26.4+.
  2. Declare SSE plus POST the carrier on Chromium-family browsers, and state this in AD-11 so B2 measures it.

  In either case, B2's pass criteria must use the real per-install CA on stock Chrome Android, never a developer flag. Otherwise the spike passes in the lab and fails in production.

---

## High

### H1 — aioquic: draft-02 era only, idle for 11 months, no Safari interop evidence (AD-11, Stack)

- **Spine says:** `aioquic 1.3.0` is the HTTP/3 and WebTransport server.
- **Current source says:**
  - **Release status:** aioquic 1.3.0 (2025-10-11) is still the latest release, and the last commit to `main` was the same day. Nearly a year with no activity.
  - **Protocol draft:** its H3 layer advertises only `SETTINGS_ENABLE_WEBTRANSPORT = 0x2B603742` plus `H3_DATAGRAM`. That is the draft-02 negotiation. It has no `SETTINGS_WT_MAX_SESSIONS` or later-draft capsules.
  - **Chromium:** negotiates draft-02 by default; draft-07 sits behind `kEnableWebTransportDraft07`, `FEATURE_DISABLED_BY_DEFAULT`. Chrome and aioquic interoperate today.
  - **Safari 26.4:** WebKit and Apple do not publish which draft Network.framework speaks. WebKit's WebTransport code sends a `wt-available-protocols` CONNECT header, a later-draft name, so a draft-02-only server may not interoperate with Safari at all.
  - **Actively maintained alternatives:**
    - `pywebtransport` 0.20.1 (2026-07-22, Apache-2.0, Python ≥3.12, its own Rust state machine, abi3 wheels for manylinux glibc 2.34+ on aarch64 and x86_64, macOS and Windows).
    - `qh3` 2.0.3 (2026-09-02, BSD). An aioquic fork that lists "WebTransport streams".
- **Sources:**
  - https://pypi.org/pypi/aioquic/json
  - https://github.com/aiortc/aioquic/blob/main/src/aioquic/h3/connection.py
  - https://api.github.com/repos/aiortc/aioquic/commits
  - https://chromium.googlesource.com/chromium/src/+/main/net/quic/dedicated_web_transport_http3_client.cc (`LocallySupportedWebTransportVersions`)
  - https://chromium.googlesource.com/chromium/src/+/main/net/base/features.cc (`kEnableWebTransportDraft07` disabled by default)
  - https://github.com/WebKit/WebKit/blob/main/Source/WebKit/NetworkProcess/webtransport/cocoa/NetworkTransportSessionCocoa.mm
  - https://pypi.org/project/pywebtransport/
  - https://pypi.org/pypi/qh3/json
- **Memlog status:** version, licence and wheels were verified. Protocol-draft compatibility with Safari 26.4, the reason WebTransport was chosen for iPhones, was never checked.
- **Fix:**
  - Mark the aioquic pin provisional and gate it on B2.
  - Add a B2 pass criterion: a stock iPhone on iOS 26.4+ (and iOS 27) plus stock Chrome and Firefox open a session, send and receive streams, and resume, all against the chosen Python server.
  - Name `pywebtransport` and `qh3` as candidates.
  - Record the server library's maintenance status as a selection criterion.

### H2 — The SSE fallback cannot carry a bearer header through `EventSource` (AD-11, AD-16)

- **Spine says:** a bearer token, never a cookie, authenticates every request, including the SSE fallback. The auth tripwires require "no token in logs".
- **Current source says:**
  - The `EventSource` constructor takes only `withCredentials`; custom request headers such as `Authorization` cannot be set.
  - Native SSE would therefore force the token into the URL query string, which lands in access logs, proxies and history and breaks the no-token-in-logs invariant, or into a cookie, which AD-16 forbids.
  - Because of C1, this fallback is also the de-facto carrier on every Chromium browser.
- **Source:** https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource
- **Memlog status:** not recorded.
- **Fix:** state in AD-11 and AD-16 that the SSE carrier is consumed through `fetch()` with a streamed `ReadableStream` and an `Authorization` header (the SSE wire format without the `EventSource` API). If a URL credential is ever needed, use a single-use short-lived stream ticket minted over an authenticated POST, and add a tripwire that strips it from access logs.

### H3 — TypeScript 7.0.2 breaks the Svelte type-check toolchain (AD-20, Stack)

- **Spine says:** `typescript 7.0.2`, with Svelte and Vite.
- **Current source says:**
  - TypeScript 7.0.2 is real: npm `latest`, released 2026-07-08. It is the Go-native compiler.
  - `svelte-check` 4.7.6, the current Svelte type checker, declares `peerDependencies.typescript: "^5.0.0 || ^6.0.0"`.
  - TypeScript 7 support is only behind `--tsgo` or `--tsgo-experimental-api`. It needs TypeScript 6 installed alongside, as `typescript@~6` plus an `@typescript/native@npm:typescript@7` alias.
  - Vite itself strips types without `tsc`, so builds still work. Type-checking `.svelte` files, the main guard against drift between stations, does not work on plain TS 7.
- **Sources:**
  - https://registry.npmjs.org/typescript (dist-tags, time)
  - https://registry.npmjs.org/svelte-check/latest (peerDependencies)
  - https://github.com/sveltejs/language-tools/pull/3036
  - https://github.com/sveltejs/language-tools/issues/2733
- **Memlog status:** the version, licence and existence were verified. Compatibility with the Svelte toolchain was not.
- **Fix:** pin `typescript` to 6.x as the type-check baseline, and add `svelte-check` (4.7.x) to the Stack table. Optionally add TS 7 through the documented `--tsgo` alias once `svelte-check` drops "experimental". Alternatively, state the dual install explicitly in the Stack table.

### H4 — Passkeys cannot bind to an IP address; the spine names no hostname plan (AD-14, AD-15, AD-17)

- **Spine says:**
  - The name constraints cover "the install's host names and its home and WireGuard IP ranges".
  - Remote access runs over WireGuard.
  - The first passkey is enrolled on the Bridge origin, and passkey sign-in is the only way to add a device.
- **Current source says:**
  - A WebAuthn RP ID must be a valid domain, equal to or a registrable suffix of the origin's host. IP addresses are explicitly not permitted, so `create()` and `get()` on `https://192.168.x.x` or a WireGuard IP throw `SecurityError`.
  - A passkey is bound to one RP ID. Home access and WireGuard access must use the same hostname, or the passkey enrolled at home will not be offered remotely.
  - `.local` mDNS names do not cross a WireGuard tunnel. The name needs DNS served through the tunnel (the WireGuard peer `DNS =`) or a name that resolves in both places.
- **Sources:**
  - https://github.com/w3c/webauthn/issues/1358 (WG decision: IP addresses not permitted in RP IDs)
  - https://www.w3.org/TR/webauthn-3/#rp-id
- **Memlog status:** not recorded; py_webauthn was checked only for version and licence.
- **Fix:** add a rule to AD-13 or AD-17.
  - The Bridge is always addressed by one install hostname, generated at setup, used as the RP ID, and valid both on the LAN and inside WireGuard. The platform provides resolution: a DNS responder advertised in the WireGuard peer config, plus the LAN route.
  - Direct-IP access shows a redirect to the hostname, never a passkey prompt.
  - IP SANs and IP name constraints remain only for non-WebAuthn paths such as the worker link.
  - Add "passkey enrolled at home is usable over WireGuard" to B1's pass criteria.

### H5 — "Router changes are never required" is asserted, but the source document lists it as unresolved (AD-15, B1)

- **Spine says:** AD-15 says "Router changes and third-party tunnels are never required", and the spine's B1 fail branch mentions only certificate trust.
- **Current source says:**
  - WireGuard does no NAT traversal: one peer must have a reachable UDP endpoint.
  - A home host behind consumer NAT needs an inbound port mapping: a manual forward, or UPnP-IGD, NAT-PMP or PCP. Behind carrier-grade NAT, even that is impossible without an outside relay.
  - The approved full picture treats this as open. §11 question 7 reads "WireGuard behind carrier-grade NAT without router changes". B1's fail condition there includes "a router change or third-party tunnel is needed", but that branch was dropped from the spine's B1 row.
- **Sources:**
  - `docs/agentic-os-dashboard/full-picture.md` lines 186 (B1 fail condition) and 213 (open question 7)
  - https://community.hetzner.com/tutorials/bypass-cgnat-with-a-wireguard-vps-relay/ (CGNAT requires an outbound relay)
- **Memlog status:** the host-OS WireGuard decision was adopted. Reachability behind NAT was never verified.
- **Fix:**
  - Restore B1's "router change or third-party tunnel needed" fail condition in the Spike gates table.
  - Mark AD-15's "never required" as provisional until B1.
  - Name the fail branch now. For example: automatic port mapping via UPnP-IGD, NAT-PMP or PCP first; then a guided self-hosted relay; home-only access stated plainly when CGNAT blocks both.

---

## Medium

### M1 — Node ^20.19.0 has been end-of-life since 2026-04-30 (Stack)

- **Spine says:** Node (build time only) `^20.19.0 or ≥22.12.0`. This copies Vite 8's `engines` field.
- **Current source says:**
  - **Node 20:** end of life 2026-04-30.
  - **Node 22:** Maintenance LTS until 2027-04-30.
  - **Node 24:** Active LTS; moves to maintenance 2026-10-20.
  - **Node 26:** becomes LTS 2026-10-28.
- **Sources:**
  - https://raw.githubusercontent.com/nodejs/Release/main/schedule.json
  - https://registry.npmjs.org/vite/latest
- **Fix:** set the supported build runtime to Node 24 LTS, with ≥22.12 as the minimum. Keep Vite's engine range out of the Stack table. Add an `engines` field or `.nvmrc` in `web/bridge/` so the drift test builds on a supported runtime.

### M2 — aiohttp 3.13.5 is one minor version behind (Stack)

- **Spine says:** `aiohttp 3.13.5`.
- **Current source says:**
  - PyPI latest is 3.14.3.
  - `uv.lock` has 3.13.5; `pyproject.toml` allows `>=3.11,<4`.
  - The pin matches the project, not upstream.
- **Sources:**
  - https://pypi.org/pypi/aiohttp/json
  - `uv.lock`
  - `pyproject.toml` line 31
- **Fix:** state that the pin reflects the lock. Either bump the lock to 3.14.x and re-run the suite before the Bridge epic, or record why 3.13.5 is held.

### M3 — UUIDv7 is not in the Python 3.13 standard library (Consistency Conventions, data ids)

- **Spine says:** `event_id` is a UUIDv7. Python is `≥3.13`.
- **Current source says:**
  - `uuid.uuid7()` was added in Python 3.14.
  - `uv.lock` does contain `uuid-utils` 0.16.0 (latest 1.0.0, BSD-3), but only as a transitive dependency. It is not declared in `pyproject.toml` and no `src/` code uses it.
  - The local venv runs 3.14.5, which would hide the gap in tests.
- **Sources:**
  - https://docs.python.org/3/library/uuid.html#uuid.uuid7 ("Added in version 3.14")
  - https://pypi.org/pypi/uuid-utils/json
  - `uv.lock`
- **Fix:** either raise `requires-python` to `≥3.14`, or declare `uuid-utils` as a direct dependency behind one id helper in `journal/`. Add a CI matrix entry on 3.13 if 3.13 stays supported.

### M4 — The usual Python Web Push libraries fail the AD-25 licence rule (AD-19, AD-25)

- **Spine says:** Web Push uses VAPID with an encrypted payload, and AD-25 allows only MIT, Apache or BSD code.
- **Current source says:**
  - `pywebpush` 2.5.0 and `py-vapid` 1.9.4 are both MPL-2.0, which the rule does not allow.
  - `http-ece` 1.2.1 (payload encryption, RFC 8291) is MIT.
  - VAPID (RFC 8292) is an ES256 JWT that `cryptography`, already a dependency, can sign.
- **Sources:**
  - https://pypi.org/pypi/pywebpush/json
  - https://pypi.org/pypi/py-vapid/json
  - https://pypi.org/pypi/http-ece/json
- **Fix:** name the Web Push implementation in the Stack table: `http-ece` plus a small VAPID signer on `cryptography`. Add MPL-2.0 to the licence tripwire's deny-list test fixtures so the choice cannot regress.

### M5 — Front-end toolchain pins missing from the Stack table (AD-20, AD-21)

- **Spine says:** svelte, three, typescript and vite only.
- **Current source says:**
  - Svelte on Vite 8 needs `@sveltejs/vite-plugin-svelte` 7.3.0, which requires `vite ^8.0.0`, `svelte ^5.46.4` and Node `^20.19 || ^22.12 || >=24`.
  - Types for three come from `@types/three` 0.186.0.
  - `svelte-check` 4.7.6 is needed for H3.
  - Without these pins, the AD-21 build-drift test is not reproducible.
- **Sources:**
  - https://registry.npmjs.org/@sveltejs/vite-plugin-svelte/latest
  - https://registry.npmjs.org/@types/three/latest
  - https://registry.npmjs.org/svelte-check/latest
- **Fix:** add these three rows and commit a `package-lock.json` or equivalent under `web/bridge/`.

### M6 — The cross-tab stream mechanism is unnamed and unverified (AD-31)

- **Spine says:** "One client store per browser owns the live stream, shared across tabs." It names no mechanism.
- **Current source says:**
  - **SharedWorker:** Safari and iOS 16+; Chrome Android only from 148. Older Android Chrome has none.
  - **Chrome 148 feature:** `extendedLifetime` for SharedWorker is Chrome-only.
  - **WebTransport inside a worker:** MDN BCD has no `worker_support` entry for Safari 26.4, so it is unverified.
  - **The alternative:** leader election with `navigator.locks` (Safari 15.4+, Chrome 69+) plus `BroadcastChannel` (Safari 15.4+, Chrome 54+) works everywhere.
  - **iOS partitioning:** an installed home-screen app and Safari tabs are separate storage partitions, so they are separate "browsers" and hold two streams.
- **Sources:**
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/SharedWorker.json
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/WebTransport.json
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/LockManager.json
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/BroadcastChannel.json
- **Fix:** name the mechanism in AD-31. Either use Web Locks leader election plus BroadcastChannel, with the stream in the leader tab, or use SharedWorker with that leader election as the fallback. Add "WebTransport and fetch streaming inside the chosen context on iOS 26.4+" and "leader hand-off on tab close or backgrounding" to B2.

### M7 — Device-token storage in IndexedDB on iOS (AD-16)

- **Spine says:** the token lives in IndexedDB, valid for about 30 days of inactivity.
- **Current source says:**
  - WebKit deletes script-writable storage, IndexedDB included, for an origin with no user interaction in 7 days of browser use. Home-screen web apps are exempt.
  - In a plain Safari tab the token can disappear well before its 30-day server lifetime, forcing a passkey sign-in.
  - Safari 17+ supports `navigator.storage.persist()`, and persistent mode exempts an origin from eviction.
  - The installed app does not share storage with Safari tabs, so each counts as its own device.
- **Sources:**
  - https://webkit.org/blog/14403/updates-to-storage-policy/
  - https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- **Fix:**
  - Call `navigator.storage.persist()` at sign-in.
  - Treat a missing token as a normal "new device" path rather than an error.
  - List installed app versus Safari tab as separate devices in the Security station.
  - Add a 7-day idle check on a Safari tab to B1.

### M8 — Apple's handling of X.509 name constraints has no current primary source (AD-14)

- **Spine says:** step 1 is a name-constrained CA, gated on B1 with a fallback order.
- **Current source says:**
  - The only Apple-specific statements are a 2016 claim that iOS and macOS reject the extension, repeated in the caniuse issue opened 2024-02-09, and BetterTLS's archived December 2021 run, which covered Safari on macOS 11.6.2 but not iOS.
  - Nothing covers iOS 26 or 27. Constraints on IP ranges (`iPAddress` subtrees) are the least-tested case.
  - The spine handles this honestly as provisional; the risk is only that B1 tests too little.
- **Sources:**
  - https://github.com/Fyrd/caniuse/issues/6969
  - https://bettertls.com/
- **Fix:** have B1 run a BetterTLS-style matrix on a stock iPhone (iOS 26.4 and 27), in Safari and the installed app:
  - a permitted DNS name;
  - an excluded DNS name;
  - a permitted IP;
  - an IP outside the permitted ranges.

  Record the result in the memlog.

### M9 — iOS Low Power Mode caps `requestAnimationFrame` at 30 fps, which skews frame-time tiering (AD-20)

- **Spine says:** the render tier comes from measured frame time plus `prefers-reduced-motion`, never from battery APIs.
- **Current source says:**
  - WebKit throttles `requestAnimationFrame` to 30 fps in iOS Low Power Mode, and Low Power Mode cannot be detected.
  - A naive frame-time measurement will read a fast phone in Low Power Mode as a slow device.
  - Rejecting battery APIs is correct.
- **Sources:**
  - https://bugs.webkit.org/show_bug.cgi?id=168837
  - https://motion.dev/magazine/when-browsers-throttle-requestanimationframe
- **Fix:**
  - Measure frame-time headroom against the observed rAF cadence (work time per frame), not raw fps.
  - Tier on sustained dropped frames.
  - Add "Low Power Mode on" as a B3 case.

### M10 — Echo cancellation is guaranteed only for audio from a peer connection (Conventions: sound)

- **Spine says:** "All Bridge audio plays through echo-cancelled output, so cues cannot trigger barge-in."
- **Current source says:**
  - `echoCancellation: true` "must attempt to cancel at least as much as `remote-only`". `remote-only` covers only audio from `MediaStreamTrack`s sourced from an `RTCPeerConnection`.
  - Cancelling cues played through Web Audio or `<audio>` (the `"all"` mode) is optional per browser.
- **Source:** https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation
- **Fix:** either route every Bridge cue and Owl's voice through the WebRTC peer connection while a voice session is open, or add "cue audio does not trigger barge-in" to S5 with a push-to-talk fail branch for that device class.

### M11 — Microphone and WebRTC in installed iOS web apps have a regression history (AD-23, S7)

- **Spine says:** browser audio travels over WebRTC; iOS rules cover the user gesture and the hidden page.
- **Current source says:**
  - Standalone-mode `getUserMedia` was historically broken (WebKit bugs 185448 and 180551).
  - An iOS 26.1 beta shipped a microphone regression ("No AVAudioSessionCaptureDevice device"), fixed in beta 2.
  - A developer-forum report says recording in a home-screen app stops working after close and reopen.
  - The spine already gates this on S7; the risk is regressions between point releases.
- **Sources:**
  - https://bugs.webkit.org/show_bug.cgi?id=185448
  - https://developer.apple.com/forums/thread/802555
  - https://developer.apple.com/forums/thread/797987
- **Fix:**
  - Make S7 include "installed app: close, reopen, mic works" on the current iOS release.
  - Re-run it on each major iOS release as a standing check, not once.

### M12 — iOS 27 ships on 2026-09-14; the spine names only 26.x (AD-11, spikes)

- **Spine says:** it refers to "pre-26.4 Safari". The spike rows name "stock iPhone" and "older iPhone" with no OS version.
- **Current source says:**
  - iOS 27 releases 2026-09-14.
  - The Safari 27 beta notes mention no changes to WebTransport, Web Push, WebAuthn, SharedWorker, storage policy or certificate handling. They do add Service Worker static routing and WebRTC `targetLatency`.
- **Sources:**
  - https://webkit.org/blog/17967/news-from-wwdc26-webkit-in-safari-27-beta/
  - https://en.wikipedia.org/wiki/IOS_27
- **Fix:** pin the spike device matrix to named OS versions: iOS 26.4+ and iOS 27 on iPhone, and the current Chrome stable (≥148) on Android.

### M13 — Passkeys, service worker and push on a private-CA origin lack primary evidence (AD-14, AD-17, AD-19)

- **Spine says:** after the CA is installed, the origin is a secure context for passkeys, the service worker, push and PWA install.
- **Current source says:**
  - WebAuthn and service workers need a secure context with no certificate error.
  - No WebKit or Chromium source states that a user-installed root satisfies WebAuthn specifically.
  - WebKit's WebTransport code does evaluate normal `SecTrust`, which is encouraging.
  - A developer-forum thread on passkeys with a self-signed local server reports friction.
  - The spine gates this on B1, which is correct.
- **Sources:**
  - https://developer.apple.com/forums/thread/711224
  - https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/PushManager.json
- **Fix:** keep B1's gate. Add explicit per-step pass rows for passkey create and get, service worker registration, push subscribe, and PWA install on iOS and Android, each with the per-install CA (H4 hostname) and nothing else.

---

## Low — confirmed or minor

| # | Item | Spine says | Current source says | Source | Fix |
| --- | --- | --- | --- | --- | --- |
| L1 | Python | ≥3.13 | `pyproject.toml` `requires-python = ">=3.13"`, and the lock agrees. The local venv runs 3.14.5. | `pyproject.toml` line 5; `uv.lock` line 3 | None; see M3 |
| L2 | svelte | 5.57.0 | npm `latest` 5.57.0 (2026-08-28), MIT | https://registry.npmjs.org/svelte | None |
| L3 | three / `three/webgpu` | 0.186.0, WebGPURenderer with WebGL2 fallback | npm `latest` 0.186.0 (2026-09-08), MIT. WebGPU: Safari and iOS 26, Chrome Android 121. Chrome on Linux: Intel Gen12+ only. Firefox 141: partial. The fallback is needed and planned. | https://registry.npmjs.org/three ; https://raw.githubusercontent.com/mdn/browser-compat-data/main/api/GPU.json | None |
| L4 | vite | 8.3.0 | npm `latest` 8.3.0, released 2026-09-10, three days before review. MIT. | https://registry.npmjs.org/vite | Consider 8.3.x patch uptake before the epic |
| L5 | webauthn (py_webauthn) | 3.0.0 | PyPI latest 3.0.0 (2026-06-29), BSD-3-Clause, Python ≥3.10 | https://pypi.org/pypi/webauthn/json | None |
| L6 | aioquic wheels | abi3 wheels listed | Matches PyPI (memlog 2026-09-13) | https://pypi.org/pypi/aioquic/json | None; see H1 for protocol fit |
| L7 | Pipecat | Provisional until S1, version deferred | `pipecat-ai` 1.10.0 (2026-09-12), BSD-2-Clause, Python ≥3.11. Its WebRTC stack `aiortc` 1.15.0 is BSD-3. | https://pypi.org/pypi/pipecat-ai/json ; https://pypi.org/pypi/aiortc/json | None; pin at S1 |
| L8 | Piper is GPL-3 | Auto-install removed | `piper-tts` 1.8.0 is GPL-3.0-or-later. `src/stackowl/media/tts/piper.py` exists. | https://pypi.org/pypi/piper-tts/json | None |
| L9 | `cryptography` (CA, name constraints) | Not in the Stack table | `pyproject` `>=42,<49`, lock 48.0.0, PyPI latest 50.0.1. AD-14 depends on it. | https://pypi.org/pypi/cryptography/json | Add a row; decide whether to lift `<49` |
| L10 | WebTransport Baseline | Safari 26.4+ | BCD: Chrome 97, Firefox 114, Safari and iOS 26.4. `serverCertificateHashes` in Safari is now confirmed, correcting the memlog's "unconfirmed". | https://webkit.org/blog/17862/webkit-features-for-safari-26-4/ ; BCD WebTransport.json | Update the memlog |
| L11 | Web Push for installed apps | Installed Bridge app | BCD PushManager: iOS 16.4, "Notifications are supported in web apps saved to the home screen." | BCD PushManager.json | None |
| L12 | VAPID and encrypted payload | Standard Web Push | RFC 8292 and RFC 8291; http-ece is MIT (see M4) | https://www.rfc-editor.org/rfc/rfc8292 ; https://www.rfc-editor.org/rfc/rfc8291 | None |
| L13 | `prefers-reduced-motion`, no battery API | Tier input | Baseline media query. The Battery Status API is absent in Safari and Firefox, so excluding it is correct. | https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion | None; see M9 |
| L14 | SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` cursor | Monotonic, never reused | Documented guarantee. With one WAL writer at a time, insert order equals commit order across the core and gateway processes. | https://sqlite.org/autoinc.html | None |
| L15 | Chrome Local Network Access | Not mentioned | LNA applies to WebTransport from Chrome 147. It gates public→private and →loopback, not a private page reaching its own private host, so no prompt is expected. | BCD WebTransport.json `local_network_access`; https://developer.chrome.com/blog/local-network-access | Add "no LNA prompt" to B2 on Chrome ≥147 |
| L16 | HTTP/1.1 connection cap for SSE | One stream per browser | Correct: browsers cap about 6 connections per origin on HTTP/1.1, and aiohttp serves HTTP/1.1 only. | https://developer.mozilla.org/en-US/docs/Web/API/EventSource | None |

---

## Memlog reconciliation

| Memlog entry | Status after this review |
| --- | --- |
| WebTransport Baseline, Safari 26.4 (version, verified) | Still true. It missed Chromium's known-root rule for private CAs (C1) and server draft compatibility (H1). |
| `serverCertificateHashes` "Safari unconfirmed" | Now confirmed: Safari 26.4 per BCD and WebKit source. This enables the C1 fix. |
| aioquic 1.3.0 version, licence and wheels (verified) | Correct, but protocol fit and maintenance were never checked (H1). |
| svelte, three, vite, typescript versions and licences (verified) | Correct. Toolchain compatibility was never checked (H3, M5). Vite's Node range includes EOL Node 20 (M1). |
| py_webauthn 3.0.0 (verified) | Correct. The RP-ID/hostname constraint was never checked (H4). |
| Brownfield "assumption": aiohttp 3.13.5 | Confirmed in `uv.lock`. Upstream is 3.14.3 (M2). |
| Brownfield "assumption": `cryptography>=42` | Confirmed: `pyproject` `>=42,<49`, lock 48.0.0 (L9). |
| Name-constraint conflicting evidence (question) | Still no current Apple source (M8). The B1 gate is correct. |
| Not in the memlog at all | UUIDv7 on 3.13 (M3); Web Push licences (M4); EventSource headers (H2); SharedWorker/cross-tab (M6); IndexedDB 7-day cap (M7); Low Power Mode rAF (M9); echo-cancellation scope (M10); iOS 27 (M12); WireGuard NAT (H5). |
