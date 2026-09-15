# Rubric review — ARCHITECTURE-SPINE.md (StackOwl Bridge)

- **Reviewer:** independent rubric walker (good-spine checklist)
- **Date:** 2026-09-13
- **Spine reviewed:** `ARCHITECTURE-SPINE.md`, status `draft`, updated 2026-09-13, AD-1 to AD-33
- **Inputs read:** `.claude/skills/bmad-architecture/SKILL.md`, `references/reviewer-gate.md`, `assets/spine-template.md`, `docs/agentic-os-dashboard/full-picture.md`, spot checks under `src/stackowl/`
- **Deterministic lint:** `lint_spine.py` returned `ok: true`, 0 findings

## Verdict

The spine is strong: a clear CQRS-around-the-one-loop paradigm, a complete read lane, and a capability map that touches every station and every §8 prerequisite. It is **not yet safe to hand to epics**, for two reasons:

- Two access-layer decisions the whole Bridge rests on are missing: the canonical origin and WebAuthn RP ID, and how a phone reaches the host remotely without a router change.
- The write lane (dispatch, action gate, consent, enqueue) is not placed across the gateway/core process boundary, in ways the brownfield code makes concrete.

| Tier | Count |
| --- | --- |
| Critical | 2 |
| High | 9 |
| Medium | 16 |
| Low | 10 |

---

## Checklist walk

### 1. Fixes the real divergence points for epics, and misses none: PARTIAL

The read lane is well covered (AD-2 to AD-6, AD-9, AD-11, AD-12, AD-24, AD-29 to AD-31). Missed divergence points:

- canonical origin and RP ID (C1)
- remote reachability mechanism (C2)
- process placement of the command lane (H1)
- approval items orphaned on core restart (H2)
- the three identity concepts (H3)
- records not stored in SQLite (H4)
- flight-recorder replay base state (H5)
- non-journal traffic on the carrier, and command-result correlation (H6)
- backpressure (H9)
- Needs-you ordering and kinds (M5)
- the "attending" predicate (M6)
- push subscription ownership (M7)
- CSP (M15)

### 2. Every Rule is enforceable and prevents its divergence: MOSTLY

- **Unenforceable as written:** the AD-3 tripwire ("any state-mutation path that records nothing") (M1); the AD-21 drift test when Vite output is not byte-reproducible (M10); the AD-25 deny-list (M14).
- **Races or stalls:** AD-16 rotate-on-use (M3); AD-9 gap fill against rollback holes (M2).
- **Contradicts another AD:** AD-1 says the actor is the device principal, while AD-17 resolves every device to the owner principal (H3).

### 3. Nothing under Deferred lets two epics diverge: MOSTLY

- "NAT traversal" narrows the reachability problem to WebRTC STUN/TURN and hides C2.
- "CA rotation, revocation and per-device trust" has no revisit condition beyond "after B1" (L9).
- The remaining Deferred items are properly contained: each has one consumer, is spike-gated, or is registry-extensible.

### 4. Named tech is verified-current: OBVIOUS ISSUES ONLY

- Node `^20.19.0` is still permitted, but Node 20 reached end of life in April 2026.
- TypeScript `7.0.2` is the native port. Toolchain fit with svelte-check and vite-plugin-svelte should be confirmed.
- UUIDv7 is required, but Python ≥3.13 has no stdlib `uuid7` (L1, L2).
- Full web verification is left to the separate reviewer.

### 5. Ratifies rather than contradicts the brownfield: PARTIAL

The spine ratifies correctly: severities in `control_plane/auth.py`, `RoutingPrompter`'s unknown-channel fallback, the unchecked `protocol_version`, the gateway's DbPool plus `DurableTaskStore`, WAL, capability probes, tripwires and runtime DDL.

It contradicts or ignores:

| Brownfield fact | Where | Finding |
| --- | --- | --- |
| Consent is decided in core at execution time | `runtime/socket_consent.py` | H1 |
| `dispatch` requires a `PipelineState` | `commands/registry.py:60` | H1 |
| TaskLoop wakes in-process, on a 5 s tick | `pipeline/durable/loop.py:94,215` | H1 |
| Identity and principal already exist | `tenancy/identity.py`, `tenancy/principal.py` | H3 |
| Kuzu is core-only | `startup/orchestrator.py:1132` | H4 |
| `login_guard.py` is not carried over | `control_plane/login_guard.py` | H7 |
| A cloud TTS backend sits behind the selector | `media/tts/cloud.py` | M13 |
| `ProgressEventFrame` has a live receiver | `runtime/gateway_link.py:448` | L3 |
| `Supervisor` supervises asyncio tasks, not OS processes | `supervisor/supervisor.py` | L6 |

### 6. Covers the driving spec's capabilities: PARTIAL

See the coverage table at the end. Every station, Needs-you, the flight recorder, the briefing, voice, access and §8 items 1–12 are mapped, but some mappings do not hold:

- Archives' md and Kuzu records (H4)
- flight-recorder replay "at any speed" from a past point (H5)
- the Comms response stream and action buttons (H6)
- Needs-you budget alerts and priority sort (M5)
- proactive-speech policy (L8)
- Missions "take over" (L7)

### 7. Every owned dimension is decided, deferred or open: PARTIAL

The Operational envelope table is good on supervision, protocol skew, health, ports, certificates, retention, committed assets and licences. Silent dimensions:

- backup and restore (H8)
- performance budgets and backpressure (H9)
- deployment and environments: dev mode, CI, where the drift test runs (M11)
- the upgrade path for existing control_plane installs: config keys, password store, bookmarked URL (M9)
- remote reachability, left undecided (C2)

### 8. Structure: GOOD, WITH NITS

- Lint is clean, AD IDs are stable and ascending, and there are no placeholders.
- The mermaid diagrams are valid by inspection; they were not machine-rendered.
- The non-standard `[ARCHITECT]` tag is used on 11 ADs and inline inside 7 `[ADOPTED]` ADs, and is never defined (M16).
- Several Rules restate product behaviour from Q-decisions rather than architecture (L4).

---

## Findings

### Critical

#### C1 — No canonical origin or WebAuthn RP ID; IP-based access breaks passkeys and splits each device in two

- **Location:** AD-13, AD-14, AD-16, AD-17; Operational envelope; spike B1.
- **Problem:** AD-13 fixes "one origin" but never says what that origin's host name is. AD-14 contemplates certificates for "home and WireGuard IP ranges", and B1 is "fresh clone, no domain". Two consequences follow:
  - WebAuthn RP IDs must be valid domain strings; IP-literal origins cannot create or use passkeys, so AD-17 fails outright on an IP origin.
  - Even with names, a phone that reaches the host at a LAN address at home and a WireGuard address remotely sees two origins. That means two IndexedDB token stores (AD-16), two service workers and push subscriptions (AD-19), two installed PWAs, and passkeys bound to one RP ID only.
  
  The identity, sign-in, notifications and front-end epics will each resolve this differently.
- **Fix:** Add an AD, "One install host name is the origin and the RP ID":
  - Rule: a stable, non-IP install host name is the Bridge origin and the WebAuthn RP ID in every posture. It resolves to the same reachable address at home and remotely. No page, token, passkey or push subscription is ever bound to an IP origin.
  - Name the resolution mechanism, or open a question gated on B1. Candidates:
    - the phone always routes through WireGuard, even at home, with the peer `DNS=` set to a platform resolver on the host's WireGuard address answering for a name under `home.arpa`;
    - the Q42 domain resolves publicly to the private WireGuard address.
  - Add to B1's pass criteria: "a passkey enrolled at home signs in remotely; one PWA install serves both".
  - Add to AD-14: certificates carry the install name, and IP SANs are never the only identity.

#### C2 — Remote reachability without router changes has no mechanism and no fail branch

- **Location:** AD-15; Deferred "NAT traversal"; spike gates row B1; Operational envelope "Private network".
- **Problem:** AD-15 promises that router changes and third-party tunnels are never required, but the host WireGuard needs inbound UDP reachable from a phone on mobile data. Behind a home NAT that needs one of:
  - a port forward (a router change);
  - PCP, NAT-PMP or UPnP auto-mapping, which is often disabled and impossible under carrier-grade NAT;
  - IPv6 plus a firewall pinhole;
  - a relay (a third-party tunnel).
  
  A dynamic public IP also needs endpoint discovery, and dynamic DNS is a third-party service. Two further gaps:
  - Full-picture §11 question 7 lists this as open, yet the spine's Deferred narrows "NAT traversal" to WebRTC STUN/TURN.
  - The spine's B1 fail branch lists only the certificate-authority fallback, dropping the full-picture B1 fail condition "a router change or third-party tunnel is needed".
  
  The remote-access epic cannot build to this rule, and whichever mechanism it picks may breach principle 7.
- **Fix:**
  - Add an Open Question addressed to Bakir: the reachability ladder, and whether Q26 or Q46 needs amending. For example, a self-hosted relay on the owner's own VPS as a named exception.
  - Add an AD fixing the attempt order with an honest stated outcome, for example: IPv6 direct → PCP/NAT-PMP/UPnP auto-map → state "remote access unavailable on this network: <reason>", never a silent failure. Also fix how the peer config learns a changed public endpoint.
  - Restore the full-picture B1 fail condition in the spike gates table and give it a fail branch.
  - Rename the Deferred item so it is clearly only WebRTC inside the tunnel.

### High

#### H1 — The command lane is not placed across processes; the brownfield consent runtime and task wake contradict it

- **Location:** AD-1, AD-10, AD-18, AD-26; container diagram.
- **Problem:** AD-1 orders severity → action gate → consent → enqueue, and the diagram puts all of it in the gateway before the gateway DbPool enqueue. Three brownfield facts conflict:
  1. `CommandRegistry.dispatch(name, args, state: PipelineState)` (`commands/registry.py:60`) is a text slash-command dispatcher that needs a core turn state. A gateway-hosted typed API has none, and AD-26's typed payload is not related to it.
  2. Consent is decided in **core**, during execution, through `runtime/socket_consent.py`, which blocks on a future while the gateway only prompts (`runtime/gateway_link.py:487`). Session grants live in core memory. A gateway-side "consent before enqueue" is either a second consent engine or needs grant state over frames, and the spine picks neither.
  3. The gateway does open its own DbPool and `DurableTaskStore` (`startup/orchestrator.py:1022-1025`), so AD-10's enqueue is feasible. But core's TaskLoop only wakes on an in-process `asyncio.Event`, or a 5 s tick (`pipeline/durable/loop.py:94,110,215`). Every Bridge button therefore waits up to 5 s, which defeats "runs at once, with undo". AD-10 forbids core frames for enqueue, so no wake path is allowed.
- **Fix:** Amend AD-1 and AD-10 with a process placement:
  - State which process runs severity and the action gate (gateway, from `authz/`).
  - State where consent is decided. Recommended: in core at COMMAND execution, through the existing `ConsentRequestFrame` path, so there is one consent engine and consent is ordered after enqueue.
  - State how a typed command maps onto `CommandRegistry`: a new typed dispatch entry that both slash parsing and the Bridge API call.
  - Add a typed, payload-free "tasks enqueued" wake frame from gateway to core as the single permitted exception, or allow `DurableTaskStore.enqueue` to notify through the link. Durable truth stays in the DbPool.
  - Add a dependency edge `bridge/ → pipeline/durable`.

#### H2 — Durable approval items outlive the in-memory consent they answer

- **Location:** AD-28, AD-18, AD-27.
- **Problem:** AD-28 makes Needs-you items survive core restarts. A pending consent prompt, though, is a future awaited inside a running core task (`runtime/socket_consent.py`), and grants die on restart by design. After a core `os.execv`, `approval` items stay open with nothing waiting on them. The first answer "resolves" an item whose requester is gone, or the re-run task opens a duplicate. The command, consent and Needs-you epics will each invent their own recovery.
- **Fix:** Add a Rule to AD-28:
  - An `approval` item is bound to its requesting task id.
  - On core start, every open `approval` whose waiter no longer exists is resolved as `expired`, with a journal event.
  - The owning task is re-parked, and it re-requests (opening a fresh item) when it resumes.
  - An answer to an expired item is refused with the item shown resolved.
  - Add `expired` to the resolution outcomes and to the `outcome` enum.

#### H3 — Three identity and principal concepts are not reconciled; the web could become a different person

- **Location:** AD-1 ("device principal is the actor"), AD-7, AD-17; Security station; §8.7.
- **Problem:** The brownfield has three distinct things:
  - `ControlPrincipal` holds severities (`control_plane/auth.py:67`);
  - tenancy `Principal` / `DEFAULT_PRINCIPAL_ID` scopes data ownership through `owner_id` (`tenancy/principal.py`);
  - `IdentityResolver` maps channel handles to an `identity_key` through the config alias map (`tenancy/identity.py`, `config/settings.py:882`). Conversation and memory continuity follow this key.
  
  AD-17 resolves `web:<device>` to the "owner's default principal" and forbids yaml aliases, but never says the web handle resolves to the Telegram owner's `identity_key`. Unmapped handles resolve to themselves, so web Comms would start a separate memory and conversation, breaking "one mind". There are also two contradictions:
  - AD-1 makes the device principal the actor, while AD-17 collapses every device to the owner.
  - AD-7 claims identity will not be scattered across `tenancy/`, but `tenancy/identity.py` already is identity.
- **Fix:** Amend AD-17:
  - Name all three concepts.
  - Every `web:<device>` and `voice:<device>` handle resolves in code to the owner's tenancy principal for data scope, the owner's `identity_key` for conversation and memory, and a device-scoped `ControlPrincipal` for severity.
  - Audit and journal actor = owner identity plus `device_id` as a separate field, and add `device_id` to the envelope.
  - State that `IdentityResolver` in `tenancy/` stays the one handle→identity seam and that `authz/identity/` only feeds it the owner mapping.

#### H4 — "Every openable target is a durable row" cannot serve md or Kuzu records

- **Location:** AD-4, AD-10, AD-29; the Archives station; §3.2 Archives row.
- **Problem:** Archives opens `USER.md`, curated owl files and knowledge-graph content. The md files are not rows, and Kuzu is core-only: the gateway never opens it (`startup/orchestrator.py:1132`, `open_graph=(self._role != "gateway")`). AD-4 routes every record open through `record_ref` = table + row id, and AD-10 routes record queries only through the gateway DbPool. An epic following AD-4 literally would add tables mirroring md content, breaking "one copy of each fact".
- **Fix:** Generalise `record_ref` to `{store, locator}`, where `store` is one of `sqlite`, `md` or `graph`:
  - SQLite records open through the gateway DbPool.
  - md records open through `StackowlHome` paths, read-only from the gateway.
  - graph records open through a typed core query frame (AD-10), with the Bridge showing "unavailable while core restarts" for those only.
  
  Drop "an epic adds a table" for md- or graph-backed targets.

#### H5 — Flight-recorder replay has no base state

- **Location:** AD-29, AD-2, AD-6; Capability map "Flight recorder".
- **Problem:** The flight recorder replays last night "exactly as recorded" from any point. AD-29's snapshot is **current** state only, and journal events are metadata deltas, not invertible to a past state. Replay from 23:10 needs the crew, jobs and open Needs-you set as they were at 23:10. The Archives epic will invent something: periodic snapshot checkpoints, rewinding events, or a neutral base, which is invented truth.
- **Fix:** Add an AD, "Replay base state":
  - Either (a) the snapshot query accepts `as_of_cursor` and reconstructs from journal plus retained rows, with a declared "unknown before retention" state;
  - or (b) the prune job writes a periodic compact state checkpoint event (metadata only) that replay starts from.
  
  Pick one. Replay never shows state it cannot derive, and says so.

#### H6 — The browser↔gateway wire contract is only half-fixed: non-journal traffic and command results

- **Location:** AD-11, AD-31, AD-32, AD-1; conventions "event envelope"; §8.10.
- **Problem:** AD-11 says both carriers deliver "the identical event envelope", and AD-31 says components never open a stream. But the `web` channel's response stream and action buttons (§8.10), voice presence and captions ("travel on the web and voice channel, never in the journal", AD-32), and heartbeats are not journal envelopes. Undecided:
  - their frame family and multiplexing on the one carrier;
  - their resume semantics, since they have no cursor;
  - how a browser learns the fate of a command it POSTed: accepted, denied by severity or policy, consent pending, done, failed. Is that the task id in the response plus journal events carrying it or the `trace_id`?
  - the HTTP error envelope, and API versioning against `protocol_version`.
  
  The Comms, Missions, voice and client-store epics will each define their own.
- **Fix:** Add an AD, "One carrier, typed channels":
  - The carrier multiplexes typed frames: `journal` (cursor-resumable), `channel` (conversation chunks, buttons, captions and presence, per session, not resumable), `heartbeat` and `hello`.
  - A command POST returns `{command_id, task_id, trace_id}` or a structured error `{code, reason, remedy}`, and its outcome arrives only as journal events carrying `task_id`.
  - The API is versioned in the hello.
  - Add these to the Consistency Conventions.

#### H7 — The brute-force brake is dropped with control_plane

- **Location:** AD-7 (auth invariants that become tripwires), AD-16, AD-17; AD-13 (every interface by default).
- **Problem:** `control_plane/login_guard.py` is the only inbound brake: a bounded, refuse-fast, per-source counter that guards the login and **setup code** routes. It shipped specifically because the bind was widened to every interface. AD-7 and AD-16 list the invariants that carry over (fail closed, origin before token, per-handler auth, uniform 401, no token in logs) and omit this one. Once control_plane is deleted, the setup code, recovery code, passkey ceremony and device-approval endpoints are unbraked on an all-interface bind.
- **Fix:** Add to AD-16's carried-over list:
  - Every unauthenticated route (setup code, recovery code, passkey begin/finish, WebTransport first-message auth) goes through one bounded, refuse-immediately guard relocated from `login_guard.py` to `authz/identity/`.
  - Refusals are uniform.
  - A tripwire fails an unauthenticated route that skips it.

#### H8 — Backup and restore is silent for every new secret and store

- **Location:** Operational envelope (no row); AD-14, AD-15, AD-16, AD-17, AD-19, AD-23.
- **Problem:** The Bridge adds host-bound, irreplaceable state:
  - the CA private key (AD-14), where losing it means every phone re-trusts;
  - the identity store: owner, passkeys, sessions, recovery-code hash;
  - VAPID keys (AD-19), where losing them breaks every push subscription;
  - WireGuard host and peer keys (AD-15);
  - voice worker tokens (AD-23);
  - the journal and Needs-you items.
  
  The only backup in the codebase is `scheduler/handlers/profile_backup.py`, which covers browser profiles only. Restoring to a new host or a fresh disk either silently locks the owner out, or copies secrets in ways the epics will not agree on.
- **Fix:** Add an Operational envelope row and a short AD, "Backup and restore":
  - List which stores are in the platform backup set: secret-store entries for the CA, VAPID and WireGuard keys; the identity, journal and Needs-you tables.
  - Say what is deliberately excluded (device sessions, which are re-enrolled).
  - Make backup a seeded job, with encryption at rest for secret material.
  - Fix the restore order and state that a restore keeps the install host name (C1), so passkeys and CA trust survive.
  
  If not decided now, move it to Deferred with a named owner and a revisit condition.

#### H9 — No performance budgets or slow-consumer policy on small hardware

- **Location:** AD-2, AD-9, AD-11, AD-24, AD-31; Operational envelope "Any hardware"; spike B2.
- **Problem:** AD-2 journals every tool call, model call, memory write and delegation hop inside the caller's write transaction (AD-24), with attention classification on the write path (AD-5). Four things are unbudgeted:
  - the added write latency and lock hold per event;
  - journal growth per day at real rates (for scale, `cost_records` already holds 134,455 rows and `job_runs` ~22k);
  - gateway CPU and memory for Python `aioquic` HTTP/3 on a Jetson;
  - per-client outbound queue size.
  
  No rule says what fan-out does when a phone on mobile data cannot keep up: block, drop, or disconnect and force a cursor resume. B2 measures memory only. The carrier and client-store epics will diverge on overflow behaviour, and emitters may add events on hot loops unchecked.
- **Fix:**
  - Add a Rule to AD-9 and AD-31: each carrier has a bounded outbound queue. On overflow the gateway drops the client's live queue, sends a `resync` frame, and the client resumes from its cursor. Fan-out never blocks recording.
  - Add a budget row: a p95 `journal.record` overhead bound and a per-day event volume bound, measured in B2 with a fail branch, plus a gateway RSS/CPU ceiling on the Jetson.
  - Add "classification is O(1), with no I/O on the write path" to AD-5.

### Medium

#### M1 — AD-3's coverage tripwire is not enforceable as written

- **Location:** AD-3.
- **Problem:** "Fails any state-mutation path that records nothing" has no mechanical definition of a mutation path, so the tripwire will be dropped or faked.
- **Fix:** Define it:
  - (a) Every migration-created table is listed in the registry with its event types, or explicitly `unjournaled` with a reason. A tripwire diffs the migration table set against the registry.
  - (b) In test mode, a DbPool commit hook fails any write transaction that touches a journaled table without a journal insert.

#### M2 — AD-9 gap fill can stall on rollback holes

- **Location:** AD-9.
- **Problem:** `AUTOINCREMENT` burns ids on rolled-back transactions, so "fill any cursor gap from the table before delivering later rows" can wait forever on a cursor that will never exist.
- **Fix:** SQLite writers are serialised, so a cursor below the highest committed cursor that is absent on read is permanently absent. The gap read treats it as a hole and moves on.

#### M3 — AD-16 rotate-on-use races

- **Location:** AD-16.
- **Problem:** Concurrent POSTs, a WebTransport session, and a response lost in a tunnel all present the pre-rotation token. That means self-inflicted 401s and device lockout.
- **Fix:** Rotate at most once per interval or per carrier session. Accept the previous token for a short overlap window. Never rotate on a request whose response may be lost without a confirm step.

#### M4 — The dependency diagram is incomplete and one edge is reversed

- **Location:** AD-7 diagram and bullets.
- **Problem:**
  - `bridge/ → pipeline/durable` is missing (the enqueue in AD-10).
  - `voice/ → journal/` is missing (the gateway records voice events, AD-9).
  - `voice/ → authz/` is missing (the voice approvals of AD-27).
  - AD-30's narrator uses "current names", which would make `journal/` import `owls/` and friends, reversing the stated direction.
- **Fix:** Add the edges. The narrator takes an injected name-resolver port, implemented by the delivery side (bridge and notifications), so `journal/` still imports no subsystem.

#### M5 — Needs-you taxonomy and ordering do not match the spec

- **Location:** AD-28, AD-5; §3.3.
- **Problem:** The spec's strip is "one priority-sorted queue" and includes budget alerts (`budget_80pct_alert`) and notify / question / review classes. AD-28's kinds are `approval`, `question`, `incident` and `device`. There is no kind for budget alerts and no sort key, so the Telegram mirror, the strip and push could order items differently.
- **Fix:** Map budget alerts to a kind, or add `alert`. Fix the order as `intensity desc, opened_cursor asc`, computed in `journal/` and returned by the snapshot.

#### M6 — "Attending" is undefined in AD-27

- **Location:** AD-27, fourth bullet.
- **Problem:** "Any run the owner is not attending" decides irreversible autonomy, but has no data definition. Authz was previously inverted on exactly this attended/unattended axis.
- **Fix:** Attended = the command's originating channel/session has a registered, live prompter with a signed-in device or chat present (AD-18). Scheduler and autonomous runs are always unattended.

#### M7 — Web Push subscription and VAPID ownership are unassigned

- **Location:** AD-19; container diagram (core `alerts → pushsvc`).
- **Problem:** Subscriptions are created by the Bridge (gateway) but sent from core. No owning table, no key location, and no pruning of subscriptions for revoked devices (the link to AD-16).
- **Fix:** Subscriptions live in `authz/identity/`, tied to device sessions and deleted on revocation. The VAPID key lives in the secret store. Name the sender process.

#### M8 — The snapshot mixes core-only state with a cursor it cannot be consistent with

- **Location:** AD-29, AD-10.
- **Problem:** The snapshot lists channels and subsystem health, which are partly core-memory state per AD-10. That state cannot be "consistent with the journal cursor" read through the gateway DbPool.
- **Fix:** The snapshot carries durable rows plus the cursor only. Core-only fields come from query frames with their own `as_of` time, and the client shows them as live-probe values.

#### M9 — No upgrade path for existing control_plane installs

- **Location:** AD-7, AD-13; Operational envelope "Upgrades".
- **Problem:**
  - AD-13 "follows the existing control-plane bind setting", but `config/control_plane_settings.py` (`control_plane.enabled`, `bind_address`, `port`) goes with control_plane.
  - The fate of the L1–L4 password store is not stated: deleted, or kept as a fallback once passkeys exist.
  - Existing bookmarks and PWA installs of the old page are not addressed.
- **Fix:** In AD-7's relocation list:
  - Settings move to a `bridge` section, with an idempotent one-time config migration.
  - The password store is deleted once the owner record exists, with setup-code mode as the recovery path.
  - The old port serves a one-line redirect or notice for one release, or state explicitly that it does not.

#### M10 — AD-21's drift test mechanism is unstated

- **Location:** AD-21.
- **Problem:** Vite output is not guaranteed byte-reproducible across Node or OS versions, and a clone has no Node, so "a test fails when source and committed build drift" cannot simply rebuild.
- **Fix:** The build writes a manifest holding a hash of every `web/bridge/` input into `bridge/static/`. The tripwire recomputes the source hash in Python and compares it. CI with Node additionally rebuilds and checks.

#### M11 — Deployment and environments are silent

- **Location:** Structural Seed / Operational envelope.
- **Problem:** Several things are unstated:
  - how a developer runs the Bridge before B1: localhost is a secure context, but with which CA and origin;
  - how `scripts/dev_ingress.py` testing drives `web` channel traffic;
  - where the front-end build and tests run in CI;
  - what the spike hosts are (the full picture says not the dev box).
- **Fix:** Add an Environments row: dev = `localhost` origin (WebAuthn allows it), same code path, no CA; CI = Node build + drift + tripwires. State that the `web` channel is drivable by `dev_ingress`.

#### M12 — WebTransport over a private-CA certificate is not in any spike's pass criteria

- **Location:** AD-11, AD-14; B1 and B2.
- **Problem:** Browser QUIC/WebTransport trust for locally installed roots has differed from TCP TLS. B1 tests CA install; B2 tests carrier behaviour; neither tests WebTransport against the private CA.
- **Fix:** Add to B1: "WebTransport connects under the per-install CA on each stock browser". Fail branch: SSE + POST is that client class's carrier, which AD-11 already allows. Optionally evaluate `serverCertificateHashes` with short-lived certificates.

#### M13 — The existing cloud TTS backend is unaddressed

- **Location:** AD-22, AD-25.
- **Problem:** `media/tts/cloud.py` is an opt-in cloud TTS backend behind the very selector AD-22 reuses. AD-25 bans only *browser* cloud speech APIs.
- **Fix:** State whether the voice worker may select it: opt-in, egress disclosed, never a default, excluded from tier probing. Or delete it, under the retired-means-deleted rule.

#### M14 — AD-25's deny-list cannot enforce a permissive-only rule

- **Location:** AD-25.
- **Problem:** A deny-list passes any unknown licence arriving through a runtime download.
- **Fix:** Use an allow-list manifest. Every bundled or downloadable artifact (package, weight, font, installer) is declared with its licence, and the tripwire fails anything undeclared or outside MIT/Apache/BSD/CC-BY/OFL.

#### M15 — No content-security invariant for a token held in IndexedDB

- **Location:** AD-16, AD-21; UI conventions.
- **Problem:** A bearer token in IndexedDB is readable by any injected script. No CSP rule exists, and a front-end epic could introduce inline scripts or `eval`. AD-21 only bans CDNs.
- **Fix:** Add a convention: a strict CSP (`default-src 'self'`, no inline script, no `eval`, `connect-src 'self'`), served on every Bridge response, with a tripwire.

#### M16 — The `[ARCHITECT]` tag is undefined, and the official mark is left as an open question

- **Location:** AD-12, AD-13, AD-18, AD-23, AD-26 to AD-33, and inline in AD-3, AD-4, AD-6, AD-7, AD-9, AD-10, AD-17; Open Question 1.
- **Problem:** The template knows `[ADOPTED]`, and the method knows `[ASSUMPTION]`. `[ARCHITECT]` is neither, so readers cannot tell whether those Rules are ratified. Separately, Open Question 1 leaves the relocation of `ICON_SVG` and its drift guard unresolved, although AD-7 already has a relocation-before-deletion rule and the mark is protected against redesign.
- **Fix:** Define the tag, or convert unratified ones to `[ASSUMPTION]` and triage them per Finalize step 4. Move `ICON_SVG`, the design tokens and the logo drift guard into AD-7's relocation list, into a named `web/bridge/` module with the guard as a `bridge/` tripwire, and close OQ1.

### Low

#### L1 — Stack staleness and fit

- **Location:** Stack.
- **Problem:** Node `^20.19.0` is allowed, but Node 20 has been end-of-life since April 2026. TypeScript `7.0.2` is the native port, and svelte-check and vite-plugin-svelte depend on the TypeScript JS API.
- **Fix:** Pin Node ≥22.12 (or the current LTS). Have the web reviewer confirm the TypeScript 7 toolchain fit, or pin 5.x for type-checking.

#### L2 — UUIDv7 has no generator

- **Location:** Conventions, "ids".
- **Problem:** Python ≥3.13 (`pyproject.toml`) has no stdlib `uuid7` (added in 3.14), and the codebase has none.
- **Fix:** Name the generator: a small internal helper or a pinned dependency.

#### L3 — `ProgressEventFrame` is not fully unwired

- **Location:** AD-33.
- **Problem:** The gateway receiver routes it to the gateway EventBus for TUI render (`runtime/gateway_link.py:448`). The steer, stop, query-running and running-state frames are confirmed exported-only (`ipc/__init__.py`).
- **Fix:** Say the receiver is repointed to the journal stream (AD-2 split-mode TUI) in the same change.

#### L4 — Rules restate product behaviour

- **Location:** AD-27, AD-32, AD-22, UI conventions.
- **Problem:** Several Rules repeat Q-decision behaviour (backchannel resume, correction semantics, the sound class rules), which lengthens the spine (584 lines) and duplicates the spec.
- **Fix:** Keep only the architectural binding (state machine placement, the command mapping, trace_id undo). Cite Q-numbers for behaviour.

#### L5 — Frontmatter departs from the template

- **Location:** Frontmatter.
- **Problem:** `companions` is missing.
- **Fix:** Add `companions: []`, or list the full picture as a companion.

#### L6 — The Supervisor does not supervise OS processes

- **Location:** AD-23; Operational envelope.
- **Problem:** `Supervisor` supervises asyncio `SupervisedTask`s, not OS processes (`supervisor/supervisor.py:60-112`).
- **Fix:** State that the voice worker is wrapped in a `SupervisedTask` that owns the subprocess.

#### L7 — Missions "take over" and "steer" are unmapped

- **Location:** Capability map, Missions.
- **Problem:** "Take over" and "steer" (§3.2) are not mapped to COMMAND types.
- **Fix:** Name them in the map, or note that they are ordinary COMMAND types added by the Missions epic.

#### L8 — Proactive speech policy has no home

- **Location:** Capability map / AD-5, AD-32.
- **Problem:** §5's rule (only about things that would notify anyway, only while the dashboard is open, always mutable) has no home, so the voice and notifications epics could diverge.
- **Fix:** One line: proactive speech consumes only `needs_you` classifications while a client store is visible, and has a mute in the client store.

#### L9 — Deferred CA rotation has no revisit condition

- **Location:** Deferred, "CA rotation, revocation and per-device trust".
- **Problem:** There is no revisit condition, and no statement that device revocation (AD-16) suffices without certificate revocation.
- **Fix:** Add both.

#### L10 — Existing voice capture is not placed

- **Location:** AD-22.
- **Problem:** TUI voice (`tui/voice/recorder.py`) and Telegram voice (`channels/telegram/voice.py`) are not placed relative to the new `voice/` package and streaming path.
- **Fix:** State that they stay on the batch STT selector, or migrate, so the voice epic does not duplicate or strand them.

---

## Brownfield spot-check evidence

| Spine claim | Code | Result |
| --- | --- | --- |
| `CommandRegistry.dispatch` is the one shared dispatch | `commands/registry.py:16,60`, `dispatch(name, args, state: PipelineState)` | Exists; signature is core-turn bound (H1) |
| Severities READ, WRITE, CONSEQUENTIAL in control_plane | `control_plane/auth.py:45-67` | Ratified |
| Unknown channel routes to the autonomous prompter | `tools/consent.py:642-685`, `RoutingPrompter.register` / fallback | Ratified (AD-18 fixes it) |
| `ConsentRequestFrame` lacks `reply_target` | `ipc/frames.py:227`, no `reply_target` | Ratified |
| Hello `protocol_version` unchecked | `ipc/frames.py:39`, no reader anywhere | Ratified |
| Consent decided where | `runtime/socket_consent.py` (core, blocks on future); `runtime/gateway_link.py:487` (gateway prompts) | Conflicts with AD-1 ordering (H1) |
| Gateway has its own DbPool | `startup/orchestrator.py:1022-1025` (gateway builds `DbPool` and `DurableTaskStore`) | Ratified |
| TaskLoop picks up new tasks | `pipeline/durable/loop.py:94,110,215`, in-process `asyncio.Event` or 5 s tick | Gap (H1) |
| control_plane lives in core | `startup/orchestrator.py:4563` (`role != "gateway"`) | AD-8 is a deliberate move, as the spec requires |
| Identity | `tenancy/identity.py` (alias map → `identity_key`), `tenancy/principal.py` (`DEFAULT_PRINCIPAL_ID`), `config/settings.py:882` | Not reconciled (H3) |
| Kuzu availability | `startup/orchestrator.py:1132`, `open_graph=(self._role != "gateway")` | Core-only (H4) |
| Inbound brute-force guard | `control_plane/login_guard.py` | Not carried over (H7) |
| Backup | `scheduler/handlers/profile_backup.py`, browser profiles only | No DB or secret backup (H8) |
| `media/stt`, `media/tts` selectors | `media/stt/selector.py`, `media/tts/selector.py`, plus `media/tts/cloud.py`, `media/tts/piper.py` | Ratified; cloud backend unaddressed (M13) |
| Capability probes | `media/image/capability.py`, `sandbox/capability.py` | Ratified |
| `Supervisor` pattern | `supervisor/supervisor.py` (asyncio tasks) | Needs a process wrapper (L6) |
| SQLite WAL / busy timeout | `db/pool.py:55-56` | Ratified |
| Runtime DDL not a precedent | `audit/logger.py:158`, `channels/telegram/callbacks.py:51` | Ratified |
| Tripwires | `scripts/tripwires.sh`, `pyproject.toml:164` marker | Ratified |
| `StackowlHome`, `infra/observability`, `HealthContributor` | `paths.py:11`, `infra/observability.py`, `health/status.py:86` | Ratified |
| Frames AD-33 deletes | Steer / Stop / QueryRunning / RunningState exported only; `ProgressEventFrame` received at `gateway_link.py:448` | Mostly ratified (L3) |
| Timestamps | Migrations mix `TEXT` ISO and `REAL` epoch | Convention scoped to new tables; fine |

## Spec coverage (full-picture)

| Spec capability | Mapped | Holds? |
| --- | --- | --- |
| Viewscreen (truthful motion, heartbeat, projection) | AD-5, AD-11, AD-12, AD-20, AD-29, AD-31 | Yes |
| Comms (web channel, delivery failures, transcripts) | AD-1, AD-4, AD-7, AD-18 | Response stream and buttons on the carrier undecided (H6); identity continuity (H3) |
| Crew (activity, authority, pause/resume/rename/grant) | AD-1, AD-2, AD-26, AD-29 | Yes |
| Missions (retry/cancel/pause/run-now/steer/take over) | AD-1, AD-2, AD-26, AD-29 | Mostly; "take over" unmapped (L7); button latency (H1) |
| Engineering (health over time, incidents, cost, processes) | AD-2, AD-10 | Yes, with the snapshot caveat (M8) |
| Archives (owner knowledge, decision ledger, recorder) | AD-2, AD-4, AD-30 | md and Kuzu records cannot open (H4) |
| Security (grants, approvals, devices, audit) | AD-16, AD-17, AD-18 | Audit actor contradiction (H3) |
| Needs-you strip (kinds, priority, mirror, intensity) | AD-5, AD-18, AD-28 | Budget alerts and sort order missing (M5); orphaned approvals (H2) |
| Flight recorder (replay at any speed) | AD-2, AD-6, AD-11, AD-30 | No base state (H5) |
| Briefing (since last looked, narrated, lit) | AD-2, AD-5, AD-30 | Yes (marker deferred, acceptable) |
| Voice (modes, stop/steer/correct, tiers, licences) | AD-22, AD-23, AD-25, AD-32 | Yes; proactive speech (L8); cloud TTS (M13) |
| Access (HTTPS, WireGuard, passkeys, device approval) | AD-13 to AD-17 | Origin and RP ID (C1); reachability (C2); brute-force brake (H7) |
| Phone notifications | AD-5, AD-19, AD-28, AD-30 | Subscription ownership (M7) |
| §8.1 event stream | AD-2, AD-3, AD-4, AD-9, AD-11, AD-24 | Yes (M1, M2) |
| §8.2 authorised control path | AD-1, AD-26, AD-27 | Process placement (H1) |
| §8.3 consent fails closed | AD-18 | Yes |
| §8.4 consent address over IPC | AD-18 | Yes |
| §8.5 HTTPS and private remote access | AD-13, AD-14, AD-15 | C1, C2 |
| §8.6 web server survives core restarts | AD-8, AD-9, AD-33 | Yes |
| §8.7 owner identity | AD-16, AD-17 | Identity alias mapping missing (H3) |
| §8.8 core query path | AD-10 | Yes |
| §8.9 time axis | AD-2, AD-6 | Yes (30-day window) |
| §8.10 `web` channel | AD-1, AD-7, AD-18 | Carrier framing (H6) |
| §8.11 streaming voice path | AD-22, AD-23, AD-32, AD-33 | Yes |
| §8.12 action metadata | AD-26, AD-27 | Yes ("attending" undefined, M6) |
