# Security review: StackOwl Bridge architecture spine

- **Reviewed:** `ARCHITECTURE-SPINE.md` (draft, updated 2026-09-13), with `.memlog.md` for decision history.
- **Also read:** `docs/agentic-os-dashboard/full-picture.md` §7, §8 and §12. Current behaviour was spot-checked in `control_plane/auth.py`, `login_guard.py`, `password.py` and `server.py`; `authz/`; `tools/consent.py`; `commands/registry.py`; `ipc/frames.py`; `notifications/recipient.py`; `config/secret_writer.py`; `channels/telegram/adapter.py`; and `media/tts/piper.py`.
- **Date:** 2026-09-13.
- **Kind:** defensive design review. Nothing was built or tested. Claims about browser and protocol behaviour are marked where a spike must confirm them.
- **Verdict:** the spine has the right skeleton: one mutation lane, severity in one dispatch, metadata-only events, a fail-closed prompter, per-device tokens, a host-held CA key, no Telegram device approval, and host-only ICE. Its rules still do not hold against the stated threats. Four decisions treat convenience signals as security controls: an on-screen tap, a bearer token kept in script-readable storage, a remote worker's transcript, and a CA key kept on the host the agent runs on. The spine also names none of the browser hardening, replay, rotation-abuse or integrity controls this kind of surface needs.

---

## 1. Threat model applied

| # | Threat | Main ADs exposed |
|---|---|---|
| T1 | Attacker on the same LAN (bind on every interface) | AD-13, AD-14, AD-15, AD-16, AD-17 |
| T2 | Stolen or lost phone | AD-15, AD-16, AD-17, AD-19 |
| T3 | XSS in the Bridge page (bearer token in IndexedDB) | AD-16, AD-20, AD-21, AD-27, AD-31 |
| T4 | Compromised or malicious remote voice worker | AD-22, AD-23, AD-27, AD-32 |
| T5 | Leaked CA key, or an installed CA misused | AD-14 |
| T6 | Replayed WebTransport or SSE session | AD-11, AD-16 |
| T7 | CSRF-style actions | AD-13, AD-16 |
| T8 | Consent bypass through an unregistered channel | AD-18 |
| T9 | Privilege escalation through the command registry or action-policy gate | AD-1, AD-26, AD-27 |
| T10 | Journal metadata leaking secrets or personal content | AD-3, AD-4, AD-6, AD-30 |
| T11 | Supply chain: vendored assets, committed build, runtime downloads | AD-21, AD-25 |
| T12 | Protocol downgrade (WebTransport to SSE) | AD-11, AD-13 |
| T13 | Brute force of the setup code or recovery code | AD-17 |
| T14 | Web Push payload exposure | AD-19, AD-30 |
| T15 | Compromise of the owner's Telegram account | AD-17, AD-18, AD-19 |
| T16 | The platform's own agent acting against its gate (prompt injection with unattended `shell`). Implied by "can take real actions"; `tools/consent.py:243` confirms interpreter launches are auto-grantable when nobody is attached. | AD-1, AD-14, AD-17, AD-27 |

T16 is added because it changes several verdicts. The gateway and core run as the same OS user as a `shell` tool that runs unattended. Anything the gateway can read or write at rest, a prompt-injected tool call can read or write too. That includes the `~/.stackowl/.secrets` 0600 fallback in `config/secret_writer.py:130-140` and the SQLite database.

---

## 2. Verdict per AD and convention

| AD / convention | Holds? | Why, in one line |
|---|---|---|
| AD-1 one mutation lane | Partly | The lane is right. It has no step-up, no sealed authorisation carried to the handler, and the requester kind is not bound to the principal. |
| AD-2 / AD-3 journal and registry | Partly | A closed actor list is good. It lacks web-device, voice-worker and autonomous actor kinds, and there is no tamper-evident path for security events. |
| AD-4 metadata only | Partly | The leak guard is a static schema check only. `str(exc)`, free-text labels and ids that embed chat ids pass it. |
| AD-6 retention | Partly | Pruned rows survive in the SQLite freelist and WAL. |
| AD-7 tripwires carried over | Partly | The carried tripwires inspect aiohttp handlers. They do not cover aioquic session handlers, CSP or query-string tokens. |
| AD-9 / AD-10 / AD-33 IPC | Partly | The link now carries events that drive Needs-you items and consent. It has no peer authentication (a local process can take the link). |
| AD-11 carriers | No | EventSource cannot send `Authorization`, so tokens will end up in URLs. Nothing bans 0-RTT replay or requires a first-message deadline or command idempotency. |
| AD-13 one port, every interface | Partly | One TLS origin is right. It lacks a pre-auth route inventory, a source-address policy, a Host allowlist and QUIC abuse limits. |
| AD-14 per-install CA | No | The key sits where the agent can read it. Branch 2 keeps a universal signing key online. Rotation and revocation are deferred. Install is trust-on-first-use. |
| AD-15 WireGuard | Partly | Where the phone's private key lives, peer AllowedIPs scope and peer revocation with the device are all unspecified. |
| AD-16 device bearer tokens | No | The token is extractable by XSS and unbound. Rotation-on-use has no reuse detection. There is no absolute lifetime, no revocation deadline and no hashed storage. |
| AD-17 owner identity | Partly | Approval anti-phishing, recovery-code strength, a global attempt budget and setup-mode re-entry are all unspecified. Telegram receives codes on re-entry. |
| AD-18 prompters fail closed | Partly | The fail-closed rule is right. The provenance auto-grant (`consent.py:796-810`) would grant `authority_widening` and `owl_build` to `web` and `voice` with no prompt. The `RoutingPrompter` fallback is still live. |
| AD-19 Telegram and Web Push | Partly | Payloads are not minimised. Subscriptions are not bound to or revoked with the device. There is no SSRF guard. It also contradicts AD-18 on device items. |
| AD-21 committed build | Partly | The drift test proves nothing without a lockfile, `--ignore-scripts` and a reproducible byte compare. |
| AD-22 / AD-23 voice worker | No | A worker token makes the worker a principal whose transcripts count as the owner's own orders. The gateway-to-worker transport is unspecified. |
| AD-25 licence rule | Partly | It governs licence, not integrity. Downloads are not pinned or hash-checked, and pickle weights are allowed. |
| AD-26 COMMAND kind | Mostly | Declared reversibility in code is good. Nothing stops non-dispatch code from enqueueing a COMMAND. |
| AD-27 action-policy gate | No | An on-screen tap is not an authentication factor. Its composition with always-ask consent is undefined. Standing authority sits on rows the agent can write. |
| AD-28 Needs-you items | Partly | First-answer-wins is right. There is no binding to the exact request shown (time-of-check to time-of-use). |
| AD-29 snapshot | Mostly | It must keep the `sensitive=True` config redaction as a tripwire. |
| AD-30 narrator | Partly | One rendering serves both the authenticated Bridge and third-party or lock-screen surfaces. |
| AD-32 voice state machine | Mostly | Captions stay off the journal (good). The actor chain is not specified. |
| Convention: auth | No | "Device bearer token on every request" is the whole rule. It has no step-up, CSP, token binding or replay protection. |
| Convention: logging | Holds | No token, secret or content goes into logs, and the rule carries over. |
| Deferred: CA rotation and revocation | Not acceptable as deferred | Once branch 2 or a phone-installed root exists, revocation is a day-one control. |

---

## 3. Findings

Tiers: **critical** means a single realistic compromise yields owner-level irreversible action or cross-site interception, with no remaining control. **High** means a realistic path to owner authority, persistence or sensitive disclosure. **Medium** means defence in depth or a narrower path. **Low** means hygiene.

### CRITICAL

#### C1: "On-screen tap" and severity are not authentication; nothing requires step-up

- **AD:** AD-27, AD-1, AD-16, AD-17, AD-28; Security station.
- **Threat:** T2 stolen unlocked phone, T3 XSS, T9, T16 agent reading or forging a session, and any leaked token.
- **Gap:** at the HTTP layer, "read-back plus an on-screen tap" is a POST that carries a bearer token. The server cannot tell it apart from a script, from another tab, or from a replayed request.

  Every `web:<device>` principal resolves to the owner in code, which today means `granted=ALL_SEVERITIES` (`control_plane/auth.py:387-391`). The severity check in AD-1 can therefore never refuse a web caller. Whoever holds a device token can:
  - run irreversible and CONSEQUENTIAL commands;
  - approve every `approval` Needs-you item, including consent the agent itself requested;
  - approve `device` items, so the attacker enrols their own device;
  - grant standing authority for irreversible actions.

  The passkey is used once, at sign-in. After that the only credential is a 30-day sliding string.
- **Smallest fix:** add one rule to AD-27 and AD-16.
  - These actions require a fresh WebAuthn assertion with `userVerification=required`:
    - any command declared CONSEQUENTIAL or irreversible;
    - resolving any `approval` or `device` Needs-you item;
    - every identity or security command: passkey add or remove, recovery-code regenerate, standing-authority grant, worker-token issue, WireGuard peer add, CA reissue, and `config set` of a sensitive key.
  - The server issues the challenge. It is single use, lives at most 120 s, and equals SHA-256 of the canonical command (type, payload, target, Needs-you id and version).
  - The gateway verifies the assertion before AD-27 runs and records the credential id and assertion id as actor evidence.
  - Reversible WRITE commands stay token-only, which keeps "the owner's own order runs at once, with undo".
  - For ordinary consequential consent, a 5-minute UV freshness window may stand in for per-action binding. Irreversible actions always bind per action.

  This is the one control a host-level agent (T16) cannot forge, because the private key lives in the phone's authenticator.

#### C2: XSS steals a portable owner credential; the spine sets no CSP, Trusted Types or token binding

- **AD:** AD-16, AD-20, AD-21, AD-31; auth convention.
- **Threat:** T3, and T2 through exfiltration before the loss.
- **Gap:** the Bridge renders strings that attackers or the agent influence:
  - Comms message text;
  - owl names and job names the agent creates;
  - web-page titles, email subjects and tool output inside records;
  - narrator sentences built from current names;
  - SVG and markdown.

  The token sits in IndexedDB. Any script on the origin can read it and use it from anywhere for 30 days, and its use refreshes it (AD-16).

  The memlog recorded the mitigation ("strict CSP with vendored-only scripts limits script-readable token theft", `.memlog.md` line 38), but it never became a rule. `grep` finds no `Content-Security-Policy` anywhere in `src/` today, and the current index is served without one (`control_plane/server.py:862-880`).

  A service worker can make an XSS persistent: it intercepts every later API call, including ones carrying `Authorization`.

  AD-31 shares the store across every same-origin context. So any agent-produced content served from the Bridge origin (files, attachments, HTML previews) runs with token access.
- **Smallest fix:** add a Rule to AD-21.
  1. Every Bridge response carries:
     - `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; require-trusted-types-for 'script'`
     - `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and a `Permissions-Policy` granting the microphone to self only.

     A tripwire asserts the header set. A spike confirms that Svelte 5, Three.js and the Vite output run under Trusted Types with one named policy that passes only compiler-generated static templates. Disable Vite's inline modulepreload polyfill.
  2. A lint tripwire bans `{@html}`, `innerHTML` and `insertAdjacentHTML` in `web/bridge/`. Narrator text and record content render as text nodes only.
  3. Record content and attachments are served with `Content-Security-Policy: sandbox`, `Content-Disposition: attachment` and `nosniff`. They are never served as active same-origin documents.
  4. The token is bound to its device. On sign-in the browser generates a non-extractable WebCrypto P-256 key (`extractable:false`), and the session binds that key's public half. Each request, and the WebTransport auth message, carries a signed proof over method, path, timestamp, server nonce and body hash (DPoP-style). A copied token string then does nothing off the device.
  5. The service worker script is served `Cache-Control: no-store`, never handles `/api/` or carrier routes, and has a versioned kill switch.

#### C3: the voice worker is trusted as the owner's voice

- **AD:** AD-22, AD-23, AD-27, AD-32, AD-18.
- **Threat:** T4, plus T1 stealing the worker token over the LAN.
- **Gap:** audio goes browser to worker directly, so the gateway never hears it and takes the worker's transcript on faith. Under AD-27 that transcript is "the owner's own order", and a reversible action runs at once. A worker that is compromised, malicious, or impersonated with a stolen worker token can:
  - inject orders with no user present;
  - trigger `authority_widening` and `owl_build` with no prompt, because `voice` becomes a gateway channel and `ConsentPolicy.request` auto-grants those for any registered channel (`tools/consent.py:796-810`);
  - read every reply through TTS and every utterance.

  "A remote worker authenticates with its own revocable worker token" makes the worker a principal. The spine does not specify the gateway-to-remote-worker transport. On a home LAN outside WireGuard, a token sent in the clear is theft-ready. The actor on voice commands is not specified either.
- **Smallest fix:** rewrite the AD-23 worker bullet as "the worker is an untrusted media processor, never a principal".
  1. The worker token's scope is `transcribe` and `speak` only. It cannot open a session, call a Bridge API, or enqueue anything.
  2. Only the gateway opens a voice session, during WebRTC signalling authenticated by a device token (and its C2 proof). It issues a per-session capability bound to the device session id, the worker id and the session's lifetime. A transcript frame without a live capability is dropped and opens an `incident`.
  3. The actor on a voice command is `owner`, then `web:<device>`, then `voice-worker:<id>`, recorded in the journal and the audit log.
  4. Voice-originated commands never satisfy the provenance auto-grant, and never run irreversible actions without the C1 step-up.
  5. The gateway and a remote worker talk over mutually authenticated TLS (a client certificate issued for the worker's name) or over WireGuard only, never plaintext LAN.
  6. Revoking a device ends its voice sessions. Worker tokens are listed, and are rotatable and revocable in the Security station.

#### C4: the CA private key sits where the agent can read it; branch 2 keeps a universal MITM key online; revocation is deferred

- **AD:** AD-14; Deferred "CA rotation, revocation and per-device trust".
- **Threat:** T5 and T16, with T1 and T2 as the payoff.
- **Gap:** "kept in the platform secret store and never leaves the host" is not a boundary against the platform's own agent. The store falls back to a 0600 file under `~/.stackowl/.secrets` (`config/secret_writer.py:130-140`), readable by the same OS user whose `shell` runs interpreters unattended (`tools/consent.py:243-244`, and the AutonomousPrompter grant path). One prompt injection can `cat` the key.
  - **Branch 1** (name-constrained): a leaked key lets the attacker impersonate the Bridge for anyone on-path. That means token and passkey-session theft from the LAN.
  - **Branch 2** (unrestricted CA on the owner's iPhone): a leaked key lets the attacker intercept **all** of the phone's TLS, banking included, wherever they are on-path.

  "Issues only short-lived certificates" is a policy the key's holder follows; a thief does not. Name constraints are the only technical limit, and branch 2 removes them. With rotation and revocation deferred, a leaked root has no kill switch beyond hand-removing the profile on each device.
- **Smallest fix:**
  1. The root key is used once. At install it signs a name-constrained intermediate that repeats the root's constraints. The root private key is then destroyed, and rotation means a new root plus a guided reinstall. The online key is the intermediate's.
  2. **Branch 2 keeps no online signing key.** The root signs one leaf with the longest validity the B1 devices accept, then is destroyed. Renewal re-runs the guided install. Auto-renewing short-lived certificates applies to branch 1 only.
  3. The intermediate key lives in the OS keyring, never in the 0600 file fallback. Where no keyring exists, the install says so and records the residual risk. The durable fix is running the gateway (CA key, identity store, seal key) as an OS user that core's tools cannot read. **That is an owner decision.**
  4. B1 pass criteria gain a negative test: a certificate for `example.com` signed by the constrained intermediate must be rejected on the stock device.
  5. Revocation stops being deferred. The install guide includes removal steps. Any unexpected issuance, or a change to the key file, opens a `needs_you` item at `high`.

### HIGH

#### H1: consent and the action policy compose unsafely

- **AD:** AD-18, AD-27, AD-26.
- **Threat:** T8, T9, T4, T3.
- **Gap:**
  - (a) The provenance rule (`tools/consent.py:796-810`, `541-551`) grants `authority_widening` and `owl_build` once, **before any prompter**, for every channel in `ChannelRegistry`. `web` and `voice` join that registry in this spine, so a bearer holder or a worker-injected transcript can permanently widen an owl's capability with no prompt. AD-18 ("no prompter fails closed") never reaches this branch.
  - (b) `RoutingPrompter.prompt` still routes unknown channels to `AutonomousPrompter` (`tools/consent.py:664-682`). AD-18 contradicts the live code but does not say the branch is deleted, and "autonomous runs keep their explicitly registered autonomous prompter" keys autonomy on a channel **string** the request carries.
  - (c) AD-27 says "the owner's own order for a reversible action runs at once". `ConsentPolicy` keeps `prompt_surface`, `destructive`, `lock`, `alarm`, `authority_widening` and `owl_build` always-ask even when reversible (`consent.py:251-284`), because an injected instruction acts before the undo. The spine does not say which rule wins.
- **Smallest fix:** add to AD-18 and AD-27:
  - The provenance auto-grant applies only to channels whose ingress authenticates a person on every message (for example, the Telegram allowlist). On `web` and `voice` those categories need the C1 step-up.
  - The `RoutingPrompter` to `AutonomousPrompter` fallback is deleted in the same change.
  - Autonomous runs carry a principal (`autonomous:scheduler`), not a channel name. The gateway sets `ConsentRequest.channel` from ingress provenance only, never from payload.
  - "The action policy may add friction and never removes an always-ask consent category." A tripwire runs every always-ask category through the run-at-once branch and expects a prompt.

#### H2: the agent can write the authority data the gate trusts

- **AD:** AD-26, AD-27, AD-17, AD-16.
- **Threat:** T9, T16.
- **Gap:**
  - (a) The AD-27 requester kind (owner vs Owl or crew) decides "runs at once" vs "read-back". Nothing binds it to the authenticated principal, and `DurableTaskStore.enqueue` is the universal ingress (memlog). An owl path that enqueues `kind=COMMAND` with an owner requester skips read-back.
  - (b) "Standing authority for a pre-authorised irreversible action is stored on the owning row." Jobs rows are writable by the agent's scheduling tools, so an owl can pre-authorise itself.
  - (c) Device sessions and passkeys live in SQLite that the agent's `shell` can read and write. Plaintext tokens can be read and replayed. A forged session row, or the agent's own passkey public key, can be inserted. Consent is then self-approvable.
  - (d) The dry-run branch in `CommandRegistry.dispatch` (`commands/registry.py:69-83`) returns before anything else, so a severity check added below it is bypassed by `??`.
- **Smallest fix:**
  - `CommandRegistry.dispatch` issues an authorisation record for each COMMAND: principal, requester kind, severity and policy decision, step-up assertion id, and command digest. The record is HMAC-sealed with a gateway key held in the OS keyring. The handler registry refuses unsealed or mismatched tasks, and a tripwire proves no tool or owl path enqueues `kind=COMMAND`.
  - Standing authority is a sealed grant record created only by a step-up command. A flag on the owning row counts only with a valid seal.
  - Session tokens and the recovery code are stored as hashes only. Passkey and session rows are sealed.
  - Every credential or session add raises an out-of-band alert through Telegram and Web Push, with no content.
  - The severity check sits above the dry-run intercept.
  - Residual: an agent running as the same OS user can in the worst case reach the keyring. Separate OS users is the durable fix; see C4.3.

#### H3: carrier authentication is unbuildable as written, and nothing stops replay

- **AD:** AD-11, AD-16, AD-7 tripwires.
- **Threat:** T6, T3, T10 (tokens in logs).
- **Gap:**
  - The native `EventSource` API cannot set an `Authorization` header. "A bearer token, never a cookie, authenticates the SSE fallback" will be implemented as `?token=` in the URL. That lands in access logs, history and proxy logs, which breaks "no token in logs".
  - WebTransport carries the token "in the first message". Nothing requires a deadline or size limit before auth, a ban on data before auth, or an Origin check on the CONNECT.
  - Nothing forbids QUIC 0-RTT. Early data is replayable by an on-path LAN attacker.
  - Command POSTs have no idempotency key, so a replayed or retried POST enqueues twice. That matters most for irreversible actions.
  - The task brief asks for a stream ticket. The spine has none.
- **Smallest fix:** add to AD-11:
  - The SSE fallback uses `fetch()` streaming with `Authorization`. Where that is impossible, use a stream ticket: 256-bit, single use, at most 30 s, bound to the device session and carrier, minted by an authenticated POST. A tripwire fails any route that reads a credential from the query string.
  - The WebTransport server disables 0-RTT and early data.
  - The CONNECT `Origin` is checked before auth.
  - The auth message must arrive within 5 s and be at most 4 KB, or the session closes. Nothing streams before auth.
  - The auth message signs the server-hello nonce with the C2 device key, so a captured auth message cannot be replayed.
  - Every command carries a client `command_id` (UUIDv7), unique-indexed in the task store. A replay returns the original result.

#### H4: token lifecycle and revocation invite silent persistence

- **AD:** AD-16, AD-17; Security station.
- **Threat:** T2, T3, T6.
- **Gap:**
  - With rotate-on-use, a thief who uses the token first rotates the owner's copy out. The spine defines no reuse detection, so the theft stays invisible and the attacker keeps a sliding 30-day session indefinitely. There is no absolute lifetime.
  - Revocation has no propagation deadline. WebTransport or SSE streams authenticated at connect keep streaming after revoke, and so do voice sessions and push subscriptions.
  - An owner whose only signed-in device is the lost phone has no host-side way to revoke. The spine names only "from a signed-in device" or the recovery code, and no L4-style CLI.
- **Smallest fix:** add to AD-16:
  - Store only SHA-256 of each token.
  - Rotate at most once every few minutes, with a grace of at most 60 s for the predecessor.
  - Presenting a retired token after the grace revokes the whole device session and opens a `needs_you` item at `high` (refresh-token reuse detection).
  - Set an absolute lifetime (for example 90 days) after which passkey sign-in is required.
  - Revocation takes effect on the next request and, within 2 s, closes that device's live carriers, voice sessions and push subscription, plus its WireGuard peer (M8).
  - Add `stackowl bridge sessions revoke --all` on the host.

#### H5: device approval can be phished and spammed

- **AD:** AD-17, AD-28, AD-18, AD-19.
- **Threat:** T1, T15, and approval fatigue.
- **Gap:**
  - Anyone on the LAN can create a new-device request. The spine does not require the approving card to prove it is the device in the owner's hand: no matching code, no device name or source network, no expiry, no single-pending limit, no rate limit. If the owner is adding a laptop while an attacker's request lands first, the owner approves the attacker.
  - AD-19 says **every** Needs-you item notifies through Telegram, while AD-18 and Q48 say device approvals are never mirrored. The notification path for `device` items is contradictory.
- **Smallest fix:** add to AD-17:
  - A pending device request displays a short matching code on the requesting device. The approver must pick or enter that code on a card showing the device name, user agent and source network (LAN or WireGuard), then pass the C1 step-up.
  - Requests expire in 5 minutes, only one may be pending at a time, and there is a global hourly cap.
  - After approval, Telegram gets an informational "a device was added" message only.
  - AD-19 excludes `device` items from Telegram and from Web Push detail.

#### H6: a compromised Telegram account can claim the install

- **AD:** AD-17, AD-18, AD-19; L1 and L3 as carried over.
- **Threat:** T15, combined with T1 or WireGuard reach.
- **Gap:**
  - Setup codes go to Telegram. A damaged identity record "returns the install to setup-code mode" (L3), and today setup mode issues and sends a fresh code on **any unauthenticated guarded request** (`control_plane/server.py:659-660`, `1328-1329`). An attacker who can corrupt the store, or who waits for disk damage, gets a claim window, and the code is delivered to the account they hold.
  - Mirrored approvals are resolved by whoever holds the allowlisted Telegram account (`channels/telegram/adapter.py:1517-1535`). So a Telegram compromise also approves Bridge-originated irreversible items first-answer-wins.

  Telegram compromise already lets the attacker order the agent. The Bridge should not add the power to claim the install.
- **Smallest fix:** add to AD-17 and AD-18:
  - The setup code goes to Telegram only on a first install, where no owner record ever existed.
  - Re-entering setup mode after damage or reset issues the code to the host terminal only, and Telegram receives "setup mode was entered" without the code.
  - Code issuance is never triggered by a network request, only at boot or by the host CLI.
  - Once a passkey owner exists, the setup-code route is closed.
  - A Telegram answer may resolve only consent that Telegram itself could request. Irreversible, authority-bearing and step-up items show "approve on the Bridge".

#### H7: the recovery code and the attempt brake are unspecified or bypassable

- **AD:** AD-17.
- **Threat:** T13, T1.
- **Gap:**
  - The recovery code approves a new device with no signed-in device, so it is the master credential. The spine sets no entropy, storage, display, regeneration or use-alert rule.
  - The inherited brake is per source address with an evicting 2048-entry map (`control_plane/login_guard.py:49-55`). An IPv6 attacker rotating addresses within a /64 gets unlimited tries.
  - The 60-bit setup code (`password.py:12`) is adequate only under a global budget.
- **Smallest fix:** add to AD-17:
  - The recovery code has at least 100 bits (for example 20 Crockford symbols). It is stored as a slow hash, shown once, and regenerated only under step-up. Each use is consumed, replaced, and alerted to Telegram and every device.
  - Setup, recovery and device-request failures share a **global** budget, for example 20 failures an hour. Past it, those routes refuse for an hour and open a `needs_you` item at `high`.
  - Per-source keys use the /64 for IPv6.

#### H8: journal and narrator content has no runtime secret guard

- **AD:** AD-4, AD-3, AD-30, AD-6, AD-19.
- **Threat:** T10, T14, T15.
- **Gap:** "A leak-guard test checks the envelope and every registered `attrs` model" is a schema check. It cannot see:
  - a `str` field holding `str(exc)` from a provider error with a key in the URL;
  - a job name like "call Dr X about the biopsy";
  - a `target_id` that is a session key embedding a native chat id (`owl:secretary:telegram:dm:72055773`, `consent.py:380-383`).

  The narrator sends the same sentence to the authenticated Bridge, to Telegram (not end-to-end encrypted, stored by a third party) and to phone lock screens. Pruned rows persist in the SQLite freelist and WAL.
- **Smallest fix:** add to AD-4:
  - `attrs` fields may be enums, numbers, durations, opaque ids, or a bounded `Label` (at most 64 characters). `journal.record` passes every `Label` through the log redactor plus a high-entropy and key-pattern detector at runtime, and records the redaction.
  - Failures carry error codes or classes, never `str(exc)`.
  - Actor and target ids are opaque, never session keys or native ids.
  - The leak test drives canary secrets through real emitters.
  - Add to AD-30: a `public` rendering (kind and count only, "A job needs you") for Telegram, Web Push and lock screens. Full text is shown only inside the authenticated Bridge.
  - Add to AD-6: prune with `PRAGMA secure_delete=ON` and a WAL checkpoint.

#### H9: the licence rule has no integrity rule

- **AD:** AD-25, AD-21.
- **Threat:** T11.
- **Gap:**
  - Runtime downloads today use a moving branch with no hash: `huggingface.co/.../resolve/main` (`media/tts/piper.py:44`, `179-200`).
  - Runtime `pip install` of unpinned packages happens in `media/tts/piper.py:146`, `tools/gui/xdo.py:150` and `media/image/local_sdxl.py:159`.
  - Model weights may arrive in pickle formats, where loading them runs code.
  - The committed-build drift test (AD-21) proves nothing unless the build is reproducible from a locked dependency tree with install scripts disabled. A malicious minified bundle, or a dependency postinstall script, passes a naive "rebuild and compare".
  - The remote voice worker installs engines on another machine under the same gap.
- **Smallest fix:** add to AD-25 "integrity joins licence":
  - Every runtime download (weights, voices, engines, the NVIDIA opt-in) is listed in a committed manifest with an immutable revision URL, SHA-256 and licence. The downloader verifies before its atomic rename.
  - Weights are accepted only as safetensors, ONNX or GGUF, never pickle.
  - Runtime pip installs are exact-pinned with `--require-hashes`, or become install-time extras.
  - Add to AD-21: a committed lockfile, `npm ci --ignore-scripts`, a pinned Node version, a CI rebuild with a byte-compare of `bridge/static/`, and an OSV scan.

#### H10: CA and WireGuard bootstrap is trust-on-first-use over the LAN

- **AD:** AD-14, AD-15.
- **Threat:** T1, T5, T2.
- **Gap:**
  - Before the phone trusts the CA, it must download it. The spine does not say how that download is authenticated. A LAN attacker who swaps the profile on an HTTP or untrusted page gets **their** root installed, which means full MITM of the phone.
  - "The platform generates WireGuard keys, peer configs and a phone QR": the phone's private key is created on the host and, unless the spine says otherwise, persists where the agent (T16) and backups can read it.
- **Smallest fix:** add to AD-14 and AD-15:
  - The host's own screen (terminal text QR), or an already-trusted Bridge device under step-up, shows one QR carrying the WireGuard peer config and the CA's SHA-256 fingerprint.
  - The CA profile is fetched through the resulting tunnel, or the guided flow checks its fingerprint before install. Never from an unauthenticated LAN page.
  - The phone's WireGuard private key is shown once and never stored on the host. Only its public key is kept.

### MEDIUM

#### M1: every interface serves the pre-auth surface with no inventory or source policy

- **AD:** AD-13, AD-7.
- **Threat:** T1, T7, DNS rebinding, and a public IP on a VPS clone.
- **Gap:**
  - AD-13 exposes several pre-auth routes on every interface: static shell, manifest, service worker, WebAuthn begin and finish, setup, recovery, device request, WebTransport accept and hello.
  - "Defaults target a fresh clone on any host" includes a VPS with a public address.
  - There is no Host allowlist. `check_origin` lets requests without an `Origin` through (`auth.py:329-330`), and the only anti-rebinding control is certificate name mismatch.
  - There are no QUIC Retry or amplification limits, connection caps or body-size caps for a Jetson-class host.
- **Smallest fix:** add to AD-13:
  - A tripwire-pinned allowlist of unauthenticated routes.
  - By default, accept sources from loopback, private ranges (RFC 1918), IPv6 ULA and link-local, CGNAT and WireGuard ranges only. Public sources get a logged remedy, and a setting can opt out.
  - A Host allowlist of the install's names and addresses.
  - QUIC address validation (Retry), plus per-source and global connection caps and request-size caps.

#### M2: WebAuthn relying-party id and origin are unpinned; IP origins cannot hold passkeys

- **AD:** AD-16, AD-17, AD-14.
- **Threat:** T1, and a misconfigured verifier that accepts any origin.
- **Gap:**
  - A WebAuthn RP ID must be a domain, not an IP literal.
  - The home LAN and WireGuard addresses differ, so a passkey made at one origin fails at the other. The pressure then pushes toward recovery-code use or permissive `expected_origin` lists.
  - UV is not required, and synced-passkey trust (the owner's platform account) is not recorded.
- **Smallest fix:** add to AD-17:
  - One install hostname resolves on both LAN and WireGuard (split-horizon through the tunnel's DNS and local resolution).
  - `expected_rp_id` is fixed and `expected_origin` is an exact list.
  - Assertions require `userVerification=required` and are refused without the UV flag.
  - Backup-eligible and backup-state flags are stored.
  - The trust base includes the owner's passkey sync account, and the spine says so.

#### M3: the downgrade path and tripwire coverage are unstated

- **AD:** AD-11, AD-13, AD-7.
- **Threat:** T12.
- **Gap:**
  - A LAN attacker can drop UDP and force SSE. That is harmless only if SSE is the same TLS origin, same auth and same checks, and the spine never forbids a plain-HTTP listener or a cross-port fallback.
  - The client may treat a TLS error as "UDP blocked".
  - The carried auth tripwires inspect aiohttp handlers, not aioquic session handlers.
- **Smallest fix:** add to AD-11:
  - No plain-HTTP listener exists.
  - Fallback is only to the same origin and port over TLS.
  - A certificate error is never a fallback trigger.
  - `serverCertificateHashes` is used only with hashes delivered over the authenticated HTTPS origin.
  - The AD-7 tripwires must cover WebTransport session handlers: origin check, auth before data, and a uniform refusal.

#### M4: Web Push subscriptions are unbound, and there is no SSRF guard

- **AD:** AD-19, AD-30.
- **Threat:** T14, T2.
- **Gap:**
  - Payload minimisation is H8. Beyond it, a subscription is not tied to a device session, so a revoked stolen phone keeps receiving alerts.
  - Where the VAPID private key lives is unspecified.
  - The push endpoint is a URL the browser supplies, and the gateway POSTs to it. That is SSRF into the LAN from any authenticated device, or from an XSS.
- **Smallest fix:** add to AD-19:
  - Each subscription row references its device session and is deleted on revoke.
  - The VAPID key lives in the secret store, under the C4.3 caveat.
  - The endpoint must be `https`, and it is refused when it resolves to loopback, private, link-local, ULA or CGNAT addresses. The check is vendor-neutral.
  - The payload is `{item_id, intensity}` plus the `public` rendering.

#### M5: web and voice actors get no tamper-evident audit attribution

- **AD:** AD-1, AD-2, AD-3, AD-23; `full-picture` §7 step 5.
- **Threat:** incident response after T2, T3 or T4.
- **Gap:**
  - The journal is prunable after 30 days and writable by any process with the database. The spine keeps it apart from the hash-chained `audit_log`, and correctly so. But the spine then gives security events no tamper-evident home.
  - The actor for web and voice is "the device principal", which resolves in code to the owner.
  - Today, consent audit records `actor=session_key` (`tools/consent.py:952-962`).
- **Smallest fix:** add to AD-1: security events go to `audit_log` as evidence, not as state. They include:
  - sign-in and step-up;
  - device request, approve and revoke;
  - passkey add and remove;
  - recovery issue and use;
  - token-reuse detection;
  - worker token issue and revoke;
  - standing-authority grants;
  - web and voice consent decisions;
  - CA issuance.

  Each carries the actor chain `owner`, `web:<device>`, `voice-worker:<id>`, `credential:<id>`, `assertion:<id>`. Add `web_device`, `voice_worker` and `autonomous` to the AD-3 closed actor list.

#### M6: the IPC link has no peer authentication, and it now carries Needs-you and consent

- **AD:** AD-9, AD-10, AD-33, AD-18.
- **Threat:** T16, or any local process as the same user.
- **Gap:** the socket accepts many connections, and each accept replaces the core link (memlog brownfield fact). A child of core's `shell` can connect as "core". It can then read owner ingress, push forged journal events, and raise fake `approval` items that are phishing by design. The spine adds more authority to this link without authenticating it.
- **Smallest fix:** add to AD-33:
  - The socket directory is 0700, and a named-pipe ACL applies on Windows.
  - The gateway checks peer credentials and accepts only the PID of the core it supervises.
  - A per-boot link secret, passed to core at exec, is verified in `HelloFrame`.

#### M7: approvals are not bound to what was shown

- **AD:** AD-28, AD-27, AD-18.
- **Threat:** T9, T16, T3.
- **Gap:**
  - An owl can change the underlying request (message text, channel, attachment) between the read-back and the tap, and first-answer-wins resolves the item regardless.
  - If Owl, a model, generates the read-back, a prompt-injected owl can misdescribe the action.
- **Smallest fix:** add to AD-28:
  - A resolution carries the item's version and the digest of the canonical request that was shown. A mismatch is refused and the item is re-shown.
  - The read-back is rendered deterministically from the command payload by the narrator, never by a model. The C1 challenge uses the same digest.

#### M8: WireGuard peer scope and revocation are not tied to the device

- **AD:** AD-15, AD-16.
- **Threat:** T2.
- **Gap:** a stolen phone keeps its tunnel into the home network after its Bridge session is revoked. Peer `AllowedIPs` scope is unspecified, and the defaults in common guides route the whole LAN.
- **Smallest fix:** add to AD-15:
  - The host allows each peer its own /32 only.
  - The phone config routes only the Bridge host address by default.
  - Peers are listed in the Security station, and revoking a device removes its peer.

### LOW

#### L1: pre-auth responses reveal that an install can be claimed

- **AD:** AD-17 as carried over.
- **Gap:** today, an unauthenticated login gets 409 `setup_required` and a guarded route gets 403 `setup_required` (`server.py:671`, `1335`). A LAN scanner learns which installs are in setup mode.
- **Fix:** pre-auth routes expose setup state only in the setup-page flow, after the origin check, under the M1 source policy.

#### L2: requests without an `Origin` are accepted on browser-only routes

- **AD:** AD-16 tripwires.
- **Gap:** `check_origin` returns true when `Origin` is absent (`auth.py:329-330`).
- **Fix:** Bridge routes that change state and are browser-only (setup, recovery, device request, WebAuthn finish, commands) require `Origin` to be present. Non-browser clients, such as the voice worker and the CLI, use their own authenticated endpoints.

#### L3: snapshot and hello can over-disclose

- **AD:** AD-29, AD-12.
- **Gap:** the snapshot and the `config set` command must keep the `sensitive=True` redaction that `_handle_config` enforces today. The server hello should not reveal a build or protocol version before auth.
- **Fix:** add a `bridge/` tripwire for the redaction, and send the hello only after the auth message.

---

## 4. Decisions only the owner can make

1. **Separate OS users** for the gateway (identity, CA intermediate key, seal key, VAPID key) and for core and its tools (C4.3, H2). This is the only complete answer to T16. Every other fix narrows the gap without closing it.
2. **Step-up friction** (C1): per-action UV for irreversible and authority-bearing actions, plus a 5-minute freshness window for ordinary consequential consent. The alternative, per-action UV for all consequential consent, is safer and noisier.
3. **Provenance auto-grant on `web` and `voice`** (H1). The 2026-08-21 rule was made for authenticated channel ingress. Extending it to a bearer token or a remote worker is a new risk decision.
4. **Branch 2 of the CA order** (C4.2). Accepting a yearly guided reinstall in exchange for no online universal signing key.

## 5. What already holds

- There is one mutation lane, no generic command endpoint, and the Bridge never writes a subsystem table (AD-1, AD-26).
- Severity sits in the one shared dispatch, not in each surface (AD-1). It needs C1 to mean something.
- Journal events are metadata-only, and records open through `record_ref` under normal authority (AD-4). It needs H8.
- A channel with no registered prompter fails closed, and `reply_target` crosses IPC (AD-18). It needs H1.
- Device approval never happens from Telegram (AD-17, Q48). It needs H5 and H6.
- Tokens are per device and revocable, never cookies, and the control_plane invariants carry over: uniform 401, origin before token, auth in each handler, no token in logs (AD-16, AD-7). It needs H3 and H4.
- The voice worker never touches `core.sock`, and media uses host candidates only, with no third-party relay (AD-22, AD-23).
- The licence deny-list covers runtime downloads, Piper is removed, and browser cloud speech is banned (AD-25). It needs H9.
- All assets are vendored with no CDN, and the existing logging rule already forbids tokens and content in logs.

## 6. Verification notes

- EventSource's lack of custom headers, WebAuthn's rejection of IP-literal RP IDs, and the replayability of TLS 1.3 and QUIC 0-RTT data are platform facts. aioquic's early-data configuration must be confirmed in code during spike B2.
- Svelte 5 and Three.js compatibility with `require-trusted-types-for 'script'` is unverified and belongs in a front-end spike.
- iOS enforcement of name constraints on an intermediate (C4.4) belongs to spike B1 as a negative test.
- The code references are to the current tree at `34e022da`. The behaviours cited are the reason each rule above must be explicit, not a claim that the Bridge code will repeat them.
