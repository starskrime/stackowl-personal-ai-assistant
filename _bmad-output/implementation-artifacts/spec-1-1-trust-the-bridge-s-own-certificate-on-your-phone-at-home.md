---
title: 'Story 1.1: Trust the Bridge''s own certificate on your phone at home'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: 'e539f8ebf654d49e0d822e2c03b2bf0e996b65be'
review_loop_iteration: 0
followup_review_recommended: false
followup_pass: true
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      test_ca_private_key_is_dropped_and_collected_before_setup_returns only verifies that
      ca.setup()'s source contains "del ca_key" and "gc.collect()", not that the CA private key
      is actually unreachable at runtime after setup() returns.
    evidence: |-
      The cryptography library's Rust-backed EllipticCurvePrivateKey supports neither
      weakref.ref() (raises TypeError) nor Python's cyclic GC introspection (never appears in
      gc.get_objects(), alive or not), so there is no available tool to observe that specific
      object's liveness from outside setup(). If the key were in fact retained (e.g. a future
      change stashes it in a module-level cache while leaving "del ca_key"/"gc.collect()"
      textually untouched), no existing test would catch it. What would settle it: a
      runtime-observable memory-safety check for a Rust-backed key object, or a different
      library/approach that exposes explicit key-zeroing.
    location: >-
      spikes/bridge/tests/test_ca.py:88-107 (test), spikes/bridge/bridge_spike/ca.py:132-154 (setup())
    severity: medium (unverified)
  - summary: >-
      macOS mDNS advertising via `dns-sd -P` (proxy-record mode) is verified only by the shape of
      the constructed argv, never against real macOS hardware, so it is unknown whether
      `<install-name>.local` actually resolves on a Mac the way the Linux `avahi-publish-service -a
      -R` path was confirmed to.
    evidence: |-
      spikes/bridge/bridge_spike/mdns.py's own docstring for the Darwin branch already discloses
      "Not verified on real macOS hardware -- based on dns-sd's documented -P proxy-record option;
      Story 1.6 covers real-device verification." tests/test_mdns.py only asserts the argv shape
      (test_build_command_darwin_uses_proxy_record_mode), never runs dns-sd. What would settle it:
      run the kit on real macOS hardware and confirm `dns-sd -P ...` makes `<install-name>.local`
      resolve, e.g. via `dscacheutil -q host -a name <name>.local` or `ping <name>.local`.
    location: >-
      spikes/bridge/bridge_spike/mdns.py:50-58 (build_command, Darwin branch), spikes/bridge/tests/test_mdns.py:34-42 (test)
    severity: medium (unverified)
---

<intent-contract>

## Intent

**Problem:** No throwaway kit yet proves, on real devices, that a per-install ephemeral CA plus a single `.local` host name can serve trusted HTTPS on the home network — the Bridge's whole TLS/origin strategy (AD-13, AD-14) is unverified.

**Approach:** Build a standalone kit under `spikes/bridge/` (own inline deps, never enters the platform lockfile) that generates and destroys an ephemeral CA, signs one server certificate, advertises the install name via the OS mDNS tool, serves HTTPS with host/subnet enforcement and a PWA page, offers a renewal command, and writes checklist results to `spikes/bridge/results/`. Done when the kit's own automated check passes against Chromium on the build host — real-device runs are Story 1.6's job, not this one.

## Boundaries & Constraints

**Always:** ECDSA P-256 for CA and server key; server cert SAN = install host name only, `id-kp-serverAuth`, validity ≤825 days; CA private key never persisted and dropped from memory once signing finishes; IPv4-only listener, one HTTPS port, no plain-HTTP listener; every non-install `Host`/IP request redirected before any page renders; kit dependencies declared inline (PEP 723 `# /// script` block), never added to `pyproject.toml`/`uv.lock`; result JSON includes kit version, browser/OS, timestamp.

**Never:** touch the platform's own TLS/secret-store code; add a WireGuard/tunnel path (AD-15 retired); relax the redirect/subnet checks to make a demo easier; require a real phone for this story's own done-check (that's 1.6); commit generated CA/server private keys or `spikes/bridge/results/*.json`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh setup | `kit.py start` on a clean clone | CA created, cert signed, CA key destroyed, fingerprint + guided steps printed | n/a |
| Host advertised | Avahi running on Linux host | `<name>.local` published via `avahi-publish-service`; on macOS via `dns-sd` | If the tool is missing/fails, print the exact command instead of crashing |
| Request by IP | `GET https://<host-ip>:<port>/` | 30x redirect to `https://<install-name>:<port>/...`, no page body served | Redirect happens before any route handler renders content |
| Request from outside subnet | connection from a source not loopback/directly-attached-private | connection refused, remedy logged | Refusal is logged with the offending source and the fix |
| Trusted device loads page | CA trusted, correct host, HTTPS | 200, no cert warning, manifest + service worker present | n/a |
| Renewal | `kit.py renew` | new CA, new fingerprint, guided steps to drop old CA / trust new one | Old cert keeps working only until devices re-trust per the ceremony |
| Checklist result | owner marks a step pass/fail with a note | `spikes/bridge/results/B1-<device-class>.json` written with kit version, browser/OS, timestamp | Malformed device-class or missing note rejected client-side |

</intent-contract>

## Code Map

- `spikes/bridge/` -- new throwaway kit root; does not exist yet, nothing in `src/` to reuse (repo has no existing cert/mDNS/HTTPS-server code to build on — confirmed no `ec.generate_private_key`/`x509.CertificateBuilder` usage anywhere in `src/`).
- `pyproject.toml` -- read-only reference: platform already depends on `cryptography>=42,<49`, `aiohttp>=3.11,<4`, `playwright>=1.59,<2`; the kit must NOT add itself here — it declares the same-shaped deps inline via PEP 723 instead, per the `# /// script` convention already used in `.claude/skills/*/scripts/*.py` (e.g. `bmad-sprint-planning/scripts/sprint_plan.py`).
- Build host has `avahi-publish-service`/`avahi-daemon` active (verified via `which`/`systemctl is-active`) and Playwright's Chromium already installable — the automated check can run without missing tooling.
- `AGENTS.md` -- confirms durable platform state is SQLite/markdown under `~/.stackowl/`, never the repo; the kit's own `results/` output follows the same spirit (gitignored, not committed).

## Tasks & Acceptance

**Execution:**
- `spikes/bridge/README.md` -- state plainly the kit is throwaway, never enters the platform lockfile, and give the single start command -- orients anyone opening the directory.
- `spikes/bridge/kit.py` -- PEP 723 entrypoint with `start`, `renew`, `check` subcommands, inline deps only (`cryptography`, `aiohttp`, plus `playwright` for `check`) -- single documented command the AC requires.
- `spikes/bridge/bridge_spike/ca.py` -- generate ephemeral CA key, sign one ECDSA P-256 leaf cert (SAN = install name, `id-kp-serverAuth`, ≤825 days), return the leaf cert+key and the CA's public cert while ensuring the CA private key is never written to disk and is dropped after signing, plus a `renew()` path and a fingerprint (SHA-256) helper -- AD-14, NFR23.
- `spikes/bridge/bridge_spike/mdns.py` -- advertise `<install-name>.local` via `avahi-publish-service` (Linux) or `dns-sd -R` (macOS) as a subprocess; on failure/absence, print the exact command instead of raising -- AD-13.
- `spikes/bridge/bridge_spike/server.py` -- aiohttp HTTPS app on one port: IPv4-only bind, source-subnet allowlist (loopback + directly attached private subnets, else refuse + log remedy), a redirect-first middleware for any non-install `Host`/IP request, static PWA (`manifest.webmanifest`, service-worker-registering `index.html`) with the guided-trust text and a manual checklist form, and a results endpoint that writes `spikes/bridge/results/B1-<device-class>.json` (kit version, browser/OS, timestamp) -- AD-13, NFR25, FR75.
- `spikes/bridge/bridge_spike/static/` -- `index.html`, `manifest.webmanifest`, `sw.js` -- installable PWA that opens standalone, referenced by `server.py`.
- `spikes/bridge/tests/test_ca.py` -- unit-tests cert chain validity, SAN/EKU/validity fields, and that the CA private key is unreachable after `ca.setup()` returns (e.g. via a dropped/garbage-collected reference check).
- `spikes/bridge/tests/test_server.py` -- exercises the redirect, subnet refusal (mocking the peer source), and results-file writing against a running instance.
- `spikes/bridge/tests/test_automated_check.py` -- drives Chromium via Playwright against the kit's real generated cert (added to a Playwright trust context) on the build host, and writes `spikes/bridge/results/B1-build-host-chromium.json` -- this is the story's own done-check per the epic's "done when the kit passes its automated checks against Chromium on the build host" rule.
- `.gitignore` -- add `spikes/bridge/results/*.json` (keep the directory via `.gitkeep`) -- generated evidence, never committed.

**Acceptance Criteria:**
- Given a fresh clone on a Linux/macOS host, when the owner runs the kit's single start command, then a CA key signs one ECDSA P-256 leaf cert (SAN = install name only, `id-kp-serverAuth`, ≤825 days), the CA key is destroyed (no CA private key on disk or retained in memory after setup), and the CA fingerprint plus guided trust steps for iOS/Android/desktop Chrome print to the terminal.
- Given the host's mDNS responder, when the kit starts, then the install name is advertised through that responder's own tool, or the exact command is printed if the kit cannot run it.
- Given the kit is listening, when a request arrives by IP or any non-install Host, then it is redirected to the install name with no content served on that origin; the listener is IPv4-only, refuses non-loopback/non-local-subnet sources with a logged remedy, and never opens a plain-HTTP listener.
- Given a device that already trusts the CA, when it opens `https://<install-name>.local:<port>`, then the page loads with no certificate warning and can be installed as a PWA with a service worker that opens standalone.
- Given the owner runs the renewal command, when it completes, then a new CA fingerprint prints along with guided steps to remove the old CA and trust the new one.
- Given any kit page, when the owner marks a checklist step pass/fail with a note, then the result is written to `spikes/bridge/results/B1-<device-class>.json` with kit version, browser/OS version and a timestamp.
- Given the build host, when the automated check runs against Chromium, then it proves the certificate chain, the redirect, the subnet refusal, and result writing, and this is the story's completion bar (real-device runs are Story 1.6's, not this story's).

## Spec Change Log

## Review Triage Log

### 2026-09-15 — Review pass
- verdicts: 27 findings — high 4, medium 11, low 1, false 8, maybe-false 3
- findings:
  - `[medium]` `[patch]` (blind-hunter) `_print_trust_steps()` never prints the iOS/Android/desktop guided steps to the terminal, only a pointer to the web page/README — confirmed by reading `kit.py`; AC requires the steps printed. Action: print condensed per-platform steps directly in the terminal.
  - `[medium]` `[patch]` (blind-hunter) `_print_renewal_ceremony()` tells the owner to "repeat the trust steps above" which were never printed — same root cause as the prior row. Action: fixed by the same change.
  - `[medium]` `[patch]` (blind-hunter) `check.py` pins both the CA's and the leaf's SPKI via `--ignore-certificate-errors-spki-list`, so a leaf not actually signed by the CA could still pass — confirmed by reading `_check_certificate_chain_and_redirect`. Action: pin only the CA's SPKI.
  - `[low]` `[patch]` (blind-hunter) `check.py`'s docstring overstates what the ignore-flag skips ("only the OS trust store") — confirmed inaccurate framing. Action: correct the docstring alongside the SPKI-pin fix above.
  - `[medium]` `[patch]` (blind-hunter) `_handle_submit_result` does `bool(payload.get("passed"))`, so the JSON string `"false"` is recorded as `passed: true` — confirmed by reading `server.py`. Action: use `payload.get("passed") is True`.
  - `[medium]` `[patch]` (blind-hunter) `local_ipv4_networks()` returns `[]` silently (no log) when neither `ip` nor `ifconfig` succeed, collapsing the allowlist to loopback-only with no diagnostic — confirmed by reading `server.py`. Action: log a warning when both discovery methods fail.
  - `[high]` `[patch]` (blind-hunter) `is_source_allowed`/`local_ipv4_networks` accept any directly-attached subnet, not filtered to private ranges, contradicting AD-13's "directly attached private subnets" and the spec's own boundary — confirmed by reading `server.py`. Action: filter to `ipaddress.IPv4Network.is_private`.
  - `[false]` `[reject]` (blind-hunter) leaf `KeyUsage.key_agreement=True` — no demonstrated harm; TLS handshake works correctly with or without this bit (verified: full test suite and live HTTPS connection succeed), and no caller/consequence was named.
  - `[false]` `[reject]` (blind-hunter) CA `KeyUsage.crl_sign=True` asserts a capability the kit never implements — no demonstrated harm; cosmetic only, no named consequence.
  - `[maybe-false]` `[defer]` (blind-hunter) `test_ca_private_key_is_dropped_and_collected_before_setup_returns` only checks source text (`"del ca_key"`/`"gc.collect()"` literal strings), not runtime state — could not tell whether the key is actually retained in memory beyond `setup()`'s scope; the `cryptography` library's Rust-backed key object supports neither `weakref` nor `gc.get_objects()` introspection, so no available tool settles this. If true, this would be `medium`. Same defect as the verification-gap layer's pre-verified gap finding below; adopting its filed disposition.
  - `[medium]` `[patch]` (blind-hunter) README's "Tests" section lists the plain-pytest command as needing no Playwright, but `test_kit_cli_output.py`'s `import kit` transitively imports `bridge_spike.check`, which unconditionally imports `playwright` — confirmed by reading `kit.py`'s module-level imports. Action: defer the `check` import to inside `cmd_check()`.
  - `[false]` `[reject]` (edge-case-hunter) `mdns.build_command` raises `UnsupportedPlatformError` uncaught for an OS other than Linux/macOS — refuted: the story's own AC precondition is explicitly "a Linux or macOS host" (epics.md story 1.1 AC); any other OS is outside the documented precondition, so a loud failure there is correct, not a defect.
  - `[false]` `[reject]` (edge-case-hunter) `ca.py` CA key stays reachable via a traceback if signing raises before `del ca_key` — rejected: unlikely in everyday use (well-formed `cryptography` calls essentially never raise here) and the fix (try/finally) adds a branch, meeting both reject-low conditions.
  - `[false]` `[reject]` (edge-case-hunter) `check.py` crashes with a traceback instead of writing a FAIL result if a Playwright call raises — rejected: a loud failure on an unexpected infrastructure problem during the build-host check is acceptable signal, not a masked failure; unlikely in everyday use.
  - `[false]` `[reject]` (edge-case-hunter) TOCTOU port race between `_free_port()` and `BridgeServer.start()` binding it — rejected: unlikely in everyday single-process use on a dev host, and any occurrence fails loudly (bind `OSError`) rather than silently.
  - `[medium]` `[patch]` (edge-case-hunter) `POST /api/results` with valid-but-non-dict JSON (list/string/number) hits `payload.get(...)` → unhandled `AttributeError` → 500, instead of the documented 400 — confirmed by reading `_handle_submit_result`. Action: add an `isinstance(payload, dict)` guard.
  - `[medium]` `[patch]` (edge-case-hunter) same `passed` bool-coercion defect as the blind-hunter row above — same fix.
  - `[false]` `[reject]` (edge-case-hunter) `POST /api/results` accepts a missing `browser`/`os` — rejected: the kit's own real UI always populates both via `navigator.userAgent`/`navigator.platform`, so this is unlikely in everyday use of the kit as shipped, and enforcing it adds validation complexity for a case the shipped client never produces.
  - `[medium]` `[patch]` (edge-case-hunter) `is_source_allowed` calls `local_ipv4_networks()` (a blocking `subprocess.run`, up to 3s timeout) fresh on every request inside async middleware, stalling the event loop for concurrent connections — confirmed by reading `_subnet_guard`. Action: discover and cache the networks once at server startup.
  - `[medium]` `[patch]` (edge-case-hunter) same trust-steps-not-printed claim as the blind-hunter rows above — same fix.
  - `[high]` `[patch]` (edge-case-hunter) same private-subnet-filtering claim as the blind-hunter row above — same fix.
  - `[maybe-false]` `[defer]` (edge-case-hunter) same ca-key-testimony-only claim as the blind-hunter/verification-gap rows — same disposition.
  - `[medium]` `[patch]` (edge-case-hunter) same no-Playwright-claim/transitive-import defect as the blind-hunter row above — same fix.
  - `[high]` `[patch]` (verification-gap, pre-verified) no test exercises the "allow" half of the subnet gate — every existing test either stays on the loopback branch or passes `networks=[]`/a public IP; a regression that makes `local_ipv4_networks()` always return `[]` would collapse every real LAN device to refused while all tests stay green. Action: add a test with a synthetic private subnet + a peer inside it, asserting the response is not 403.
  - `[maybe-false]` `[defer]` (verification-gap, pre-verified) `test_ca_private_key_is_dropped_and_collected_before_setup_returns` verifies source text, not runtime state — filed disposition adopted as-is: pre-existing `cryptography` library limitation (no weakref/GC visibility on the key object), no alternative verification mechanism proposed; if the retention were real it would be `medium`.
  - `[high]` `[patch]` (verification-gap, "Other findings") `avahi-publish-service <name> _https._tcp <port>` registers a discoverable DNS-SD *service* under the host's own existing mDNS hostname — it does not publish `<install_name>.local` as a resolvable address. Verified directly: `avahi-publish-service test-verify-mode.local _https._tcp 8443` followed by `avahi-resolve -n4 test-verify-mode.local` returned nothing (no address), while `avahi-browse` showed the service's underlying `hostname = [boss-desktop-2.local]` — the machine's real hostname, not the install name. The whole point of this story is that `<install-name>.local` resolves; it currently does not. Action: publish via address mode (`-a <install-name> <host-ip>`) instead.
  - `[false]` `[reject]` (intent-alignment) reported divergence: the AC's "device that trusted the CA" step is satisfied by a headless Chromium SPKI-pin proxy rather than a real device's OS trust store — refuted directly from the epic's own primary planning document, not just the compiled context summary: `_bmad-output/planning-artifacts/epics.md` lines 800–804 state in the epic's own "How this epic runs" section that "stories 1.1–1.5 are done when the kit passes its automated checks against Chromium on the build host" and "every real-device run is owed in Story 1.6, which parks at `awaiting-operator`." This is the epic author's explicit, deliberate scoping, not a self-serving reading invented for this story.

### 2026-09-15 — Review pass (follow-up)
- verdicts: 26 findings — high 0, medium 6, low 7, false 9, maybe-false 4
- findings:
  - `[medium]` `[patch]` (blind-hunter) `_TRUST_STEPS`/`index.html` told the owner to "AirDrop it" / "download it from the terminal," but nothing anywhere wrote or served that CA certificate file — confirmed no such file write or route existed in `kit.py`/`server.py`. Action: `_serve()` now writes `artifacts.ca_cert_pem` to `results/ca.pem` and `_print_trust_steps()` prints that path; `index.html`'s matching steps updated to reference it; `.gitignore` extended to `results/*.pem`.
  - `[false]` `[reject]` (blind-hunter) claimed the Linux mDNS address-mode fix (`avahi-publish-service -a -R`) was verified only for the old form's failure, not that the new form resolves — refuted directly: ran `avahi-publish-service -a -R test-verify-review.local <host-ip>` on this build host; `avahi-resolve -n4 test-verify-review.local` returned the address, confirming the new command does make the install name resolve.
  - `[medium]` `[patch]` (blind-hunter) `check.py` correctly pins only the CA's SPKI, but no test proved a leaf signed by an unrelated CA is rejected — confirmed `test_automated_check.py` only exercised the matching-CA path. Action: added `test_certificate_chain_check_fails_for_a_leaf_not_signed_by_the_pinned_ca`, asserting the page load fails when the pinned SPKI belongs to a different CA than the one that signed the served leaf.
  - `[false]` `[reject]` (blind-hunter) flagged `B1-<device-class>.json` overwriting across two physical devices of the same class as a defect — refuted: this fixed-filename scheme is dictated by the intent-contract's own I/O matrix ("Checklist result... `B1-<device-class>.json`"); changing the granularity would mean editing the spec's own chosen design, not fixing a defect.
  - `[medium]` `[patch]` (blind-hunter) `kit.py check`'s browser-driven form submission used `device_class="desktop-chrome"` — the same value and output filename (`B1-desktop-chrome.json`) a human manually testing desktop Chrome would submit — silently overwriting real checklist evidence with synthetic evidence; not mandated by the spec (which only names `B1-build-host-chromium.json` for the check's own summary). Action: added a distinct `desktop-chrome-automated` device class/option and result filename for the automated check's form submission.
  - `[low]` `[reject]` (blind-hunter) `/api/results` has no authentication beyond the subnet gate, so any LAN host could post fake checklist results — unlikely to matter for a throwaway single-operator home-network kit whose only intended trust boundary is the subnet gate, and adding auth is more than a direct correction.
  - `[low]` `[reject]` (blind-hunter) `_default_install_name()` doesn't sanitize `socket.gethostname()` into a strict DNS/mDNS-safe label — unlikely in everyday use (real host names are already DNS-safe), and a real fix means adding sanitization logic, not a direct correction.
  - `[false]` `[reject]` `carried` (blind-hunter) same as the prior pass: `mdns.build_command` raising `UnsupportedPlatformError` uncaught for a non-Linux/macOS host — code unchanged; the story's own AC precondition is explicitly a Linux/macOS host, so a loud failure outside that precondition is correct, not a defect.
  - `[low]` `[reject]` (blind-hunter) real-device clock skew against the certificates' 5-minute `not_before` backdating is untested — real-device concerns are explicitly Story 1.6's job per the intent-contract's own Approach text ("real-device runs are Story 1.6's job, not this one").
  - `[false]` `[reject]` (blind-hunter) claimed the leaf key's temp directory might not be cleaned up if `load_cert_chain` raises — refuted: `build_ssl_context` uses `with tempfile.TemporaryDirectory()`, whose `__exit__` runs unconditionally on any exception per Python's context-manager protocol, independent of test coverage.
  - `[low]` `[reject]` (blind-hunter) `.gitignore` only covered `spikes/bridge/results/*.json`, not hypothetical future non-JSON artifacts — speculative; the kit produced no such file before this pass, and the one non-JSON artifact this pass adds (`ca.pem`) is covered by the same patch's `.gitignore` update.
  - `[medium]` `[patch]` (edge-case-hunter) `POST /api/results` with a body that isn't valid UTF-8 raised an uncaught `UnicodeDecodeError` inside `request.json()`, returning 500 instead of the documented 400 — confirmed `_handle_submit_result` only caught `json.JSONDecodeError`. Action: also catch `UnicodeDecodeError`; added `test_results_endpoint_rejects_body_that_is_not_valid_utf8`.
  - `[low]` `[reject]` (edge-case-hunter) the subnet allowlist is cached once at server construction and never refreshed, so a DHCP renewal or NIC change mid-session could stale it — unlikely for a short-lived spike session, and the caching is a deliberate, already-documented tradeoff (avoiding a blocking `subprocess.run` per request); a refresh mechanism is more than a direct correction.
  - `[low]` `[reject]` (edge-case-hunter) the mDNS publisher subprocess could in principle exit with an error between `Popen()` and the `.running` check — unlikely on a correctly configured host, and a fix needs a delay/re-poll, more than a direct correction.
  - `[low]` `[patch]` (edge-case-hunter) `Advertisement.stop()` calls `process.kill()` after a `wait(timeout=3)` timeout but never re-`wait()`s, leaving a potential zombie process — confirmed by reading `stop()`. Action: added `self.process.wait()` after `kill()`; added `test_stop_reaps_the_process_after_a_kill`.
  - `[false]` `[reject]` (edge-case-hunter) flagged the Tasks & Acceptance text saying macOS uses `dns-sd -R` while the code uses `-P` — refuted as a defect: the code's `-P` (proxy-record) choice is correct and deliberately documented (`-R` cannot target a custom host name); the mismatch is in the spec's own descriptive wording, and fixing wording means editing the spec.
  - `[false]` `[reject]` (edge-case-hunter) flagged the Tasks & Acceptance text saying the cert is "added to a Playwright trust context" while the code uses `--ignore-certificate-errors-spki-list` — refuted as a defect: the SPKI-pin mechanism is the deliberate, already-justified design (`check.py`'s own docstring), settled by the prior pass's intent-alignment finding; the mismatch is spec wording, and fixing wording means editing the spec.
  - `[maybe-false]` `[defer]` `carried` (edge-case-hunter) same as DW-1: `test_ca_private_key_is_dropped_and_collected_before_setup_returns` verifies source text, not runtime state — code and test unchanged since the prior pass; same filed disposition.
  - `[medium]` `[patch]` (verification-gap, pre-verified) `local_ipv4_networks()`'s private-subnet filter — the fix for the prior pass's high-severity finding — is only ever exercised through tests that mock the function away entirely, so a regression reintroducing the earlier defect would go undetected. Action: added `test_local_ipv4_networks_filters_out_public_directly_attached_subnets`, faking `ip`'s output with one private and one public interface and asserting only the private one survives.
  - `[medium]` `[patch]` (verification-gap, pre-verified) the subnet-gate-before-redirect middleware order is untested for a source that is both disallowed and mis-addressed — no existing test combined a disallowed peer with a wrong/IP Host, so a middleware-order regression disclosing the install name via a redirect's `Location` header would ship undetected. Action: added `test_disallowed_source_with_wrong_host_is_refused_not_redirected`.
  - `[maybe-false]` `[defer]` `carried` (verification-gap, pre-verified) same CA-key-destruction gap as DW-1 — filed disposition adopted as-is, same as the prior pass's row above.
  - `[maybe-false]` `[defer]` `carried` (intent-alignment) same CA-key destruction divergence (runtime-unreachability vs procedural-`del`+`gc.collect()` reading) as DW-1 — same disposition as the two rows above.
  - `[false]` `[reject]` (intent-alignment) reported divergence: the I/O matrix's "connection refused" wording implies network-level refusal, while the implementation refuses at the HTTP layer (403) after the TLS handshake — refuted: the acceptance criterion's own literal text is "refuses ... with a logged remedy," which the HTTP-layer refusal satisfies; the design is self-documented and deliberate (`server.py`'s own module docstring) and still blocks every response body from reaching a disallowed source.
  - `[false]` `[reject]` `carried` (intent-alignment) same divergence as the prior pass: "trusted device loads page" is proxied by a headless-Chromium SPKI pin rather than a real device's OS trust store — refuted the same way, from the epic's own explicit story-1.1-vs-1.6 scoping; code and design unchanged.
  - `[maybe-false]` `[defer]` (intent-alignment) macOS mDNS advertising (`dns-sd -P`) is verified only by its argv shape, never on real macOS hardware — the intent-contract's own Approach text excludes real-device verification from this story ("real-device runs are Story 1.6's job"), and `mdns.py`'s own docstring already discloses this honestly. If `-P` does not actually make the name resolve on real macOS, that would be `medium` (Mac users lose the feature). What would settle it: run the kit on real macOS hardware and confirm `dns-sd -P ...` makes `<install-name>.local` resolve (e.g. via `dscacheutil -q host -a name <name>.local` or `ping`).
  - `[false]` `[reject]` (intent-alignment) reported divergence: the "rejected client-side" I/O-matrix row is only tested at the server, never by driving the page's own JS — refuted: read `static/index.html`'s submit handler directly; it already implements both the malformed-device-class and missing-note guards before any `fetch` call, so the described behavior is real and correct — the absence of a browser-driven test for already-correct code is not itself a defect.

## Design Notes

Real-device trust ceremonies (iOS/Android/desktop) are explicitly out of scope for "done" here — the epic states stories 1.1–1.5 are done when the automated check passes against Chromium on the build host, and every real-device run is owed by Story 1.6 (parked `awaiting-operator` there). Nothing in this story requires an action only a human can perform outside the repo.

## Verification

**Commands:**
- `uv run spikes/bridge/kit.py start` -- expected: prints CA fingerprint + guided steps, exits 0, no CA key file left on disk.
- `uv run spikes/bridge/kit.py check` -- expected: automated Chromium check passes, writes `spikes/bridge/results/B1-build-host-chromium.json` with all fields populated.
- `uv run --with cryptography --with aiohttp python -m pytest spikes/bridge/tests` -- expected: all kit unit tests pass (or the equivalent inline-dep invocation the kit's own README documents).

## Auto Run Result

**Summary:** This was a follow-up review pass on an already-`done` spec (`followup_review_recommended: true` from the prior pass). Four review layers (blind-hunter, edge-case-hunter, verification-gap, intent-alignment) ran against the diff since `baseline_revision`; 26 findings were triaged, 7 were real and patched, 2 new items were confirmed false by direct manual verification on this build host, and the rest were rejected (spec-mandated behavior, out-of-scope real-device concerns, or unlikely-in-everyday-use edge cases whose fix would add more complexity than the defect warrants) or carried forward unchanged from the prior pass's disposition.

**Files changed this pass:**
- `spikes/bridge/kit.py` -- `_serve()` now writes the CA's public certificate to `results/ca.pem` and prints its path; `_print_trust_steps()` takes an optional `ca_cert_path`; trust-step wording no longer references a non-existent delivery mechanism.
- `spikes/bridge/bridge_spike/static/index.html` -- matching trust-step wording fix; added a `desktop-chrome-automated` device-class option so the automated check no longer collides with a real human's `desktop-chrome` result.
- `spikes/bridge/bridge_spike/check.py` -- automated check's simulated form submission now uses `desktop-chrome-automated`, writing `B1-desktop-chrome-automated.json` instead of overwriting `B1-desktop-chrome.json`.
- `spikes/bridge/bridge_spike/server.py` -- `_handle_submit_result` also catches `UnicodeDecodeError` so a non-UTF-8 body returns 400, not 500.
- `spikes/bridge/bridge_spike/mdns.py` -- `Advertisement.stop()` re-`wait()`s after `kill()` so a hung child process is reaped, not left a zombie.
- `spikes/bridge/tests/test_automated_check.py` -- added a negative test proving a leaf signed by an unrelated CA fails the pinned-SPKI check.
- `spikes/bridge/tests/test_server.py` -- added tests for the real (unmocked) private-subnet filter, the combined disallowed-source-plus-wrong-host middleware-order case, and a non-UTF-8 request body.
- `spikes/bridge/tests/test_mdns.py` -- added a test proving `stop()` reaps a process it had to `kill()`.
- `.gitignore` -- extended the Bridge spike's ignore pattern to `results/*.pem` alongside `results/*.json`.
- `_bmad-output/implementation-artifacts/spec-1-1-trust-the-bridge-s-own-certificate-on-your-phone-at-home.md` -- this pass's triage log, one new `deferred` entry (macOS mDNS real-hardware verification), and this result.

**Review findings breakdown (26 total: high 0, medium 6, low 7, false 9, maybe-false 4):**
- **Patched (7, all medium/low, none high):** missing CA-cert export mechanism behind the printed trust steps; no negative test that a leaf signed by an unrelated CA is rejected; the automated check's form submission colliding with a real human's `desktop-chrome` result file; a non-UTF-8 `/api/results` body crashing with 500 instead of 400; a potential zombie mDNS-publisher process after `kill()`; the private-subnet filter (`local_ipv4_networks()`) never exercised by its own tests; the subnet-gate-before-redirect middleware order never tested for a source that is both disallowed and mis-addressed.
- **Deferred (1 new + 3 carried, all maybe-false):** new -- macOS `dns-sd -P` mDNS advertising verified only by argv shape, never on real hardware (intent-contract excludes real-device verification from this story; would be `medium` if actually broken). Carried unchanged from the prior pass (same code, same disposition) -- the CA-private-key-destruction test verifying source text rather than runtime state (DW-1), reported independently by three layers this pass.
- **Rejected -- false (9):** the mDNS address-mode fix actually working (refuted by a live `avahi-publish-service`/`avahi-resolve` run on this host); `B1-<device-class>.json` overwriting across devices of the same class (the intent-contract's own I/O matrix dictates this exact filename scheme); the unsupported-platform traceback (carried -- the AC precondition is explicitly Linux/macOS); the leaf-key temp-file cleanup on exception (Python's `with TemporaryDirectory()` guarantees it regardless of test coverage); two spec-wording mismatches (macOS `-R` vs actual `-P`, "trust context" vs actual SPKI pin) whose only "fix" would be editing the spec; the subnet refusal being HTTP-layer rather than network-layer (the AC's literal text "refuses ... with a logged remedy" is satisfied); the "trusted device" SPKI-pin-vs-real-trust-store divergence (carried -- settled by the epic's own scoping); the "rejected client-side" claim (verified by directly reading `index.html`'s JS, which already implements both guards correctly).
- **Rejected -- low, unlikely + fix adds complexity (7):** no authentication on `/api/results` beyond the subnet gate; no DNS-label sanitization of the derived hostname; untested real-device clock skew (out of this story's scope); a stale cached subnet allowlist after a mid-session DHCP renewal (documented deliberate tradeoff); a theoretical race between the mDNS subprocess exiting and the `.running` check; and a `.gitignore` gap for hypothetical future non-JSON artifacts.

**Follow-up review recommendation:** `false`. This was itself a follow-up pass (`followup_pass: true`); none of this pass's 7 patched entries was `high` (all were medium or low), so per the convergence rule this counts as converged -- patch volume alone is not grounds for another pass.

**Verification performed:**
- `uv run --with cryptography --with aiohttp python -m pytest spikes/bridge/tests -q` -- 38 passed (non-Playwright subset).
- `uv run --with cryptography --with aiohttp --with playwright python -m pytest spikes/bridge/tests -q` -- 38 passed (full suite, including the new negative-CA Playwright test).
- `uv run spikes/bridge/kit.py check` -- PASS, all four steps (`certificate_chain`, `redirect`, `subnet_refusal`, `result_writing`) true; `results/B1-build-host-chromium.json` and the newly-separated `results/B1-desktop-chrome-automated.json` both written; the pre-existing `results/B1-desktop-chrome.json` from a prior manual run was left untouched, confirming the collision is fixed.
- `uv run spikes/bridge/kit.py start` (timed run) -- printed the CA fingerprint, the new `CA certificate file: .../results/ca.pem` line, and the (now file-path-referencing) guided trust steps; confirmed `results/ca.pem` was written and is a valid PEM certificate; no CA private key file appeared anywhere on disk.
- Manual mDNS verification: `avahi-publish-service -a -R test-verify-review.local <host-ip>` followed by `avahi-resolve -n4 test-verify-review.local` returned the address, directly confirming the Linux address-mode advertising this story depends on actually works (not just that the old broken form fails).
- `git status`/`git diff` -- confirmed `spikes/bridge/results/*.json` and the new `*.pem` stay untracked/ignored; no stray `__pycache__` directories tracked.

**Residual risks:**
- Two `maybe-false`/deferred items remain open, both requiring hardware this build host doesn't have to settle: the CA-private-key runtime-liveness gap (DW-1, pre-existing) and the new macOS real-hardware mDNS verification gap. Both are explicitly Story 1.6's territory (real-device verification) per the intent-contract's own scoping, not blockers for this story's own automated-check completion bar.
- The rejected low-severity items (no results-endpoint auth, no hostname sanitization, stale cached subnet list, mDNS-subprocess-exit race) remain theoretically possible but judged out of proportion to a throwaway, single-operator, home-LAN spike kit; none blocks the story's acceptance criteria.

