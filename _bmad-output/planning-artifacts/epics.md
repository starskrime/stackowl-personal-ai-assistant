---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
inputDocuments:
  - docs/agentic-os-dashboard/full-picture.md
  - _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-09-12/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/bridge-mockup/BRIEF-identity-and-engine.md
  - _bmad-output/planning-artifacts/bridge-mockup/BRIEF-helm-dial-v2-approved.md
  - docs/research/agentic-os-dashboard/01-agentic-os-and-jarvis-interface.md
  - docs/research/agentic-os-dashboard/02-platform-surface-inventory.md
  - docs/research/agentic-os-dashboard/03-voice-conversation-spike.md
---

# StackOwl Bridge

## Overview

This document provides the complete epic and story breakdown for stackowl-personal-ai-assistant, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

**Citation key.** `§n` = section of `full-picture.md`; `Q`/`L`/`A` = its §12 decisions (A-decisions supersede where marked); `AD-n` = ARCHITECTURE-SPINE decision; `IB` = BRIEF-identity-and-engine; `HD` = BRIEF-helm-dial-v2-approved; `[01]`/`[02]`/`[03]` = supporting research. Superseded decisions (Q9 "from anywhere", Q26 WireGuard, the Q42/Q46 free-domain fallback, Q16's on-screen tap for approvals answered in Telegram, and Q48's "never from Telegram" for new-device approvals) are not requirements; their A1/A2/A8/A11 replacements are.

## Requirements Inventory

### Functional Requirements

**Postures and Viewscreen**

FR1: Desktop shows the full Bridge: the Viewscreen, all six stations and the Needs-you strip on top. The six station names sit on the Helm Dial's outer rim and open the station sheet, with the Crew panel and Ship's log beside the dial. [§3, Q8, Q20, A10]
FR2: The phone opens on the compact Viewscreen plus the Needs-you strip, with Comms one swipe away and a bottom bar holding the six stations plus Now and Log. [§3, Q8, Q38, A10]
FR3: The Viewscreen shows the live ship: each owl and what it is doing, missions in flight, scheduled jobs approaching their due time (projection, not just state), comms traffic arriving from channels, and engineering health. [§3.1, Q12; [01] §4.3 rule 8]
FR4: Every moving or drawn mark is caused by a real recorded event or snapshot record and, when tapped, opens its own station record (the station sheet opened on that record); a target that no longer exists opens as "expired", never as an error. [§2 P1, §3, §3.1, Q6, A10; AD-4]
FR5: A quiet or failing ship looks quiet or failing: idle owls show as idle with their last activity, stopped traffic (such as delegation) is not animated, and no activity is ever invented. [§2 P1, §3.1, Q19]
FR6: Idle breathing of the owl mark and the Viewscreen follows the server heartbeat, never the browser render loop; when the stream is stale, breathing stops and the age of the last event is shown; when core is offline past the announced grace, the Bridge shows "ship offline". [§2 P1, §6, Q6; AD-12]

**Stations**

FR7: Comms station: the owner converses with Owl in a `web` conversation channel that is the same conversation, memory and task loop as Telegram; it browses Telegram, Slack and TUI threads (transcripts over the existing sessions and messages), and shows delivery failures and the undelivered outbox. [§3.2, §8.10, Q4; [02] gap 11]
FR8: Crew station: shows each owl's activity, authority (owl bounds ∩ creation ceiling), skills and DNA, and offers pause, resume, rename, grant and take over of the owl's mission (FR89); grant is always-ask and needs read-back plus a signed tap or an explicit Telegram approval (FR36). [§3.2, A8, A9]
FR9: Missions station: shows tasks (including finished-task history), schedules with their next due time, retries and dead letters, and offers pause, steer, take over (FR89), retry, cancel and run now. Jobs whose state is inconsistent (for example a completed one-shot job still enabled with a next run in the past) are listed as they are. [§3.1, §3.2, §8.9, A9]
FR89: "Take over" pauses the owl's mission and hands the owner its current plan to edit and resume, or to cancel; a taken-over mission never continues silently and stays paused until the owner resumes or cancels it. It is offered in Missions and on the owl in Crew. [§3.2, Q21, A9; AD-26, AD-27]
FR10: Engineering station: shows providers and models, health over time (a time axis, not a snapshot), self-healing incidents (what failed, what Owl tried, why no heal held, including heals that succeeded), cost as fuel, processes, and core-only live values (breakers, context windows, worker occupancy). [§1, §3.2, §8.8, §8.9]
FR11: Archives station: shows what the platform knows about the owner (`USER.md` and curated owl files), the decision ledger with per-turn history, and the flight recorder. [§3.2]
FR12: Security station: shows active authority (live session consent grants from core, never persisted, plus standing authority), approval history, sign-ins and devices with revocation, voice-worker tokens (listed, rotatable, revocable) and the audit trail. [§3.2, §7; AD-16, AD-23, AD-27]
FR13: *(Proposal; order deferred to epic sequencing.)* The first control actions are job pause/resume/run-now, task retry/cancel, owl pause/resume, skill enable/disable, config set (sensitive values redacted) and command dry-run (`??`, evaluated only after the severity check); `/bye`, `/provider`, `/connect`, owl grant, `/memory forget` and `/cost privacy` stay consent-gated. [§3.2; AD-1, AD-18, AD-29; Spine Deferred]

**Needs-you strip**

FR14: One priority-sorted Needs-you queue (ordered by intensity, then by time opened) appears on every screen and is the only place the accent colour appears. [§3.3, Q21, Q34; AD-28]
FR15: The queue holds pending consent approvals, clarifying questions (`clarify_ask`), incidents needing a decision, failures Owl could not heal, irreversible actions the owner did not set up beforehand, new-device approvals and budget alerts, as the item kinds `approval`, `question`, `incident`, `alert` and `device`. [§3.3, Q36, Q41; AD-28]
FR16: A failure Owl could not heal is a Needs-you item at high intensity; a failure Owl healed never enters the strip and appears only in its record. "Unhealed" is a recorded give-up event (such as `heal.exhausted`, `task.dead_lettered`, `job.parked`), never the absence of a heal. [§2 P2, Q34; AD-5]
FR17: An unhealed-failure card shows what failed, what Owl tried and why no heal held, with its actions (for example resume the job or keep it paused) and undo where Owl itself paused it, while its undo window is open (FR88). [§1, §3.3, A9; IB §3]
FR18: An approval card shows Owl's deterministic read-back of exactly what will happen (for example channel, text and attachment), the same text the Telegram message carries, and the control the request needs: a spoken "yes" or the confirm tap for a request that is reversible and not consequential (FR35); for an irreversible action, a consequential request or spoken order (even when reversible) or an owl-authority grant, the signed confirm tap, with an explicit Telegram approval as the alternative (FR36). [§1, §5, A7, A8; AD-27, AD-30]
FR19: An item is answered once across every surface (strip, Telegram, voice, notification deep link): the first answer wins, an answer to a resolved or expired item returns the winning outcome, and an answer to a request that changed after it was shown is refused and the item is re-shown. Items survive core restarts and expire with the request that waits on them. [§11.10, Q21; AD-28]
FR20: Telegram, as the authenticated owner channel, may approve any request, every step-up item of FR36 included, from anywhere: its buttons message carries the same deterministic read-back as the Bridge card, and the first answer wins against the Needs-you strip (FR19). Residual risk, stated: a hijacked Telegram account can approve irreversible actions. New-device approvals can also be answered in Telegram (FR69). [§3.3, §11.10, A8, A11; AD-18, AD-27, AD-28]

**Alerts and phone notifications**

FR21: Every Needs-you item is delivered through both Telegram and Web Push, so no alert depends on the push relay alone. [§2 P7, §3.3, A5; AD-19]
FR22: Delivery per item kind: `approval` goes to Telegram as the prompter's buttons message, answerable there (FR20), and to Web Push; `device` goes to Telegram as a buttons message showing the device name and matching code (FR69) and by Web Push to signed-in devices only; `question`, `incident` and `alert` go to Telegram as narrated text and to Web Push. [§3.3, A5, A8, A11; AD-19]
FR23: When an item resolves, its Telegram message is edited and its push notification is replaced (tagged by item id). [AD-19]
FR24: At home, tapping a Web Push notification opens the Bridge straight on that item. [§3, Q38, A5; AD-19, AD-28]
FR25: Away from home, where the Bridge is unreachable, tapping a Web Push notification shows a cached, metadata-only summary of the item from the Bridge's service worker, with "open at home" and a link to continue in Telegram, where an approval can be answered (FR20); there is no error page and no item content is cached. [§2 P4, §3.3, A5, A8, S7; AD-19]
FR26: Web Push payloads and lock-screen text carry metadata only: item id, kind, intensity and the narrator's public rendering (kind and count). [A5; AD-19, AD-30]

**Flight recorder and briefing**

FR27: The flight recorder in Archives replays the Bridge from recorded events at any speed, including a roughly 30-second time-lapse of a past window (for example last night): every run, every failure, every pause, exactly as recorded. Before the oldest retained checkpoint it shows "unknown before retention" and never invents state. [§1, §3.4, Q15; AD-35]
FR28: On opening, the Bridge shows a short briefing of what changed since the owner last looked (runs, failures, heals, unhealed or paused work, finished work, waiting approvals), on screen first, with each item lighting on the Viewscreen as it is named. A per-owner "last looked" marker drives it. [§1, §4 Briefings, Q12, Q37]
FR29: The briefing is spoken after the owner's first tap, or at once if hands-free is already on. Reopening a backgrounded phone takes one tap, which also starts the spoken briefing. The briefing is not proactive speech. [§4, §5, Q37]

**Owl, anticipation and undo**

FR30: The Secretary is the ship's host voice, named "Owl" by default and renamable; other owls are crew with their own identities and speak only when addressed. [§4 Host, Q10, Q18]
FR31: Owl acts on its own only within authority already granted, shows what it did on a card, offers undo where the action has an inverse (for the window of FR88), and turns anything beyond its authority into a Needs-you request. [§4 Anticipation, Q11, A9]
FR32: Owl takes an irreversible action on its own only where the owner explicitly set it up beforehand (standing authority, such as a scheduled daily report that sends a message); each such run leaves a tappable record. Any other irreversible action in a run the owner is not attending becomes a Needs-you approval. [§4, Q36; AD-27]
FR33: Every action declares whether it is reversible and what its undo is, and scheduled work records which irreversible actions the owner explicitly set it up to take. [§8.12; AD-26]
FR34: The owner's own order for a reversible action (typed, tapped or spoken) runs at once, with no read-back, and is shown on a card with undo (FR88); a spoken owner order at CONSEQUENTIAL severity instead gets Owl's read-back and then requires the signed on-screen tap in the Bridge or an explicit Telegram approval, even when reversible (FR41). [§5, Q35, Q49, A9, A12; AD-27]
FR35: A request from Owl or a crew member needs Owl's deterministic read-back before the owner's answer counts. After the read-back, a spoken "yes" approves only requests that are reversible and not consequential, even though voice enters as `voice-unverified`, and the card shows undo (FR88). [§4, Q36, Q49, A7; AD-27]
FR36: Severity wins over reversibility. Irreversible actions, consequential approvals (any request at CONSEQUENTIAL severity, even when reversible), owl-authority grants and new-device approvals require the signed on-screen tap in the Bridge or an explicit Telegram approval (FR20), each after its read-back; the tap is cryptographically signed by the signed-in device over a single-use, short-lived nonce bound to the exact request; a spoken yes after read-back approves only requests that are reversible and not consequential. [§5, §7, Q16, A3, A7, A8, A11; AD-27, AD-37]
FR37: Standing authority is granted and revoked only by explicit owner commands that need a signed tap or an explicit Telegram approval; owl-authority grants never come through voice; no owl or tool can grant authority to itself. [§4, Q36, A7, A8; AD-27, AD-41]
FR88: Undo stays available on an action's card until the action is superseded (a later change to the same target) or 24 hours pass, whichever comes first; after that the card no longer offers undo and an undo request is refused. [§4, Q11, A9; AD-26, AD-27]

**Voice**

FR38: Push-to-talk works on every surface and every hardware tier; hands-free is a toggle (open mic with voice-activity detection, end-of-turn detection and barge-in); there is no wake word; voice is English only. [§5 Modes, Q7, Q27]
FR39: A mic-live indicator shows whenever audio is captured. [§5; [01] §5.2]
FR40: Transcripts appear live and auto-send. [§5, Q32, Q35]
FR41: A spoken order can trigger at most a reversible, non-consequential action on its own (it runs at once with undo and no read-back); a spoken order at CONSEQUENTIAL severity gets Owl's read-back and then requires the signed on-screen tap in the Bridge or an explicit Telegram approval, even when reversible; a spoken "yes" after Owl's read-back approves only requests that are reversible and not consequential (FR35); irreversible actions, consequential approvals, owl-authority grants and new-device approvals require the signed on-screen tap in the Bridge or an explicit Telegram approval (FR36), and owl-authority grants never come through voice. Voice can request anything within that rule. [§5, Q16, Q35, Q49, A7, A8, A11, A12; AD-27]
FR42: Every tier gives a non-spoken acknowledgement within about 300 ms of detected end of turn: the owl mark switches to thinking and the soft acknowledgement sound plays. It is produced locally, never by a model call. [§5 Long tasks, Q39; AD-32]
FR43: A spoken "on it" follows as fast as the hardware allows (under a second on GPU and Mac); after that Owl speaks only at meaningful milestones, which needs progress events that carry more than a step name, index and total. [§5, Q39; [02] §5.2]
FR44: The instant the owner starts speaking, Owl's speech pauses; speaking over Owl never pauses or stops the task by itself. [§5, Q24, Q40]
FR45: The utterance then goes through Owl's normal understanding, never a keyword or word list, which decides whether it is a stop (the task stops), a steer (the running task carries on with the change), a new question, a transcript correction, or a backchannel or noise. [§1, §5, Q40; AD-32]
FR46: A stop, steer, new question or correction drops the paused speech; a backchannel ("mm-hm") or noise resumes it where it left off. [§5, Q47]
FR47: After a correction ("no, I said…"), Owl applies it and answers the corrected sentence fresh; it never continues an answer to words the owner did not say. [§5, Q50]
FR48: If a misheard order already ran, the correction automatically undoes the reversible action(s) that utterance triggered, shows the undo on its card, then carries out what the owner actually said. [§5, Q52; AD-27]
FR49: Proactive speech covers only things that would notify the owner anyway, only while the Bridge is open and visible, and is always mutable. [§5, Q7, Q37; AD-5]
FR50: When the phone is backgrounded, the mic indicator goes dark, hands-free shows as paused, ambient cues fall silent and any unspoken reply is delivered as text; one tap on reopening resumes and starts the spoken briefing. [§1, §5, Q37, Q51, S7; AD-23]
FR51: One server-side voice state machine drives both audio and on-screen presence, so screen and speech never disagree. [§5 Turn-taking; [01] §5.3; AD-32]
FR52: Owl's own voice and sounds never trigger barge-in. [§11.8, S5]
FR53: The platform probes the host, picks the best voice tier and states its cost honestly; hands-free warns honestly when slow; when the latency budget is missed or WebRTC fails, voice falls back to push-to-talk and states the reason. [§5 Hardware ladder, Q27, S8; AD-22, AD-23]
FR54: Speech can run on a separate machine on the home network, which authenticates to the platform. [§5, §7, Q27; AD-22, AD-23]
FR55: Each owl has a distinct bundled voice, Owl's being the most distinctive; voices are never cloned. [§4 Voices, Q30, Q31]
FR56 (superseded 2026-09-14, C35): NVIDIA-licensed speech models are never bundled; on NVIDIA hardware the owner may choose to download one after its licence is shown. [§2 P6, §5, Q44; AD-25]
FR57 (superseded 2026-09-14, C35): The GPL Piper engine is replaced as the default TTS by a permissive engine, and its automatic install is removed. [§5, §8.11, Q25; AD-25]
FR58: TUI and Telegram voice capture keep working on the batch speech-to-text selector. [AD-22]

**Look and sound (behaviour)**

FR59: The owl mark shows the presence states idle, listening, thinking, speaking, needs-you (normal and high intensity), link stale and ship offline, driven by the voice and liveness state machine. [§6 Host presence, Q23; AD-12, AD-32]
FR60: Sound has two classes, both with volume control and respecting silent mode: ambient cues (very soft texture for ordinary events, on by default, never repeating, ducking under Owl's voice, silent when backgrounded, switchable off on their own) and the Needs-you alert (the only attention-grabbing sound). The acknowledgement sound is feedback and belongs to neither class. [§2 P2, §6 Sound, Q14, Q39, Q51]
FR61: The Viewscreen renders with WebGPU and falls back to WebGL 2 automatically; every other screen is lightweight DOM; a low-power 2D mode shows the same events with less spectacle. [§6 Rendering, Q22; AD-20]

**Access, sign-in and devices**

FR62: The Bridge is reachable only on the home network, over IPv4 and HTTPS, with no built-in WireGuard, no remote access and no relay; away from home Telegram is the conversation surface, can answer approvals (FR20) and carries notifications. Router changes and third-party tunnels are never required. [§2 P4, §7 Reachability, A1, A8]
FR63: Guided setup creates a private per-install certificate authority, signs the Bridge server certificate with it once and destroys the CA key, announces a local host name on the home network, and guides trust on each device with a CA-fingerprint comparison against the host terminal. [§7, §8.5, A2; AD-13, AD-14]
FR64: Certificate renewal creates a new CA and guides a re-trust ceremony on each device (fingerprint comparison, previous CA removed); a Needs-you item opens 30 days before the server certificate expires. [§7, A2; AD-14]
FR65: WebTransport uses short-lived certificates identified by fingerprint, generated and rotated automatically. [§7, A2; AD-14]
FR66: Sign-in uses passkeys plus a one-time recovery code; passkey sign-in is needed only on a new device, or after revocation or expiry. [§7, Q17; AD-16]
FR67: At first setup the platform shows a one-time setup code in its terminal and sends it to the single allowed Telegram user (terminal or CLI only when there are none or several); entering it registers the first passkey. It reuses the L1 setup-code mechanism rather than a second one. [§7 Sign-in, Q41, L1; AD-7, AD-37]
FR68: The host CLI can issue a terminal-only setup code; no network request can mint one; setup mode exists only while no passkey exists or after the identity store is damaged (FR74). [AD-37]
FR69: A new device is approved from an already signed-in Bridge device, as a Needs-you device item confirmed by matching the device name and a short code shown on both screens with a signed tap; with the recovery code; or from Telegram, where the message shows the device name and the same short matching code displayed on the new device and the owner gives an explicit Telegram approval. Residual risk, stated: a hijacked Telegram account plus home-network access could enrol a device and gain full Bridge control. Only one device request may be pending at a time. [§7, Q41, A11; AD-37]
FR70: Each signed-in device gets a session token bound to a non-extractable key created in its browser, so a copied token is useless elsewhere; tokens have a lifetime cap and token reuse is detected (the device is revoked and a high-intensity Needs-you item opens). [§7, A4; AD-16]
FR71: Revocation from the Security station, from reuse detection or from `stackowl bridge sessions revoke` on the host refuses that device's next request and ends its streams, voice sessions and push subscription within one heartbeat interval. [AD-16]
FR72: The recovery code is shown once; each use consumes and replaces it and alerts the owner; it is regenerated only under a signed tap. [§7; AD-37]
FR73: There is one owner record, formalising today's single-allowed-Telegram-user rule; web and voice sessions resolve to the owner's principal and identity, so the Bridge is the same person, conversation and memory as the Telegram owner. [§7, §8.7, Q3, Q4; AD-17]
FR74: An unreadable identity store (backend I/O error, locked keyring, permission failure) is never treated as "no owner" and never enters setup mode: sign-in is refused with a remedy (503), the lookup is retried on the next attempt, and the failure is reported to self-healing as an incident. Telegram enrolment is never re-enabled: a damaged store (bytes readable but failing schema or integrity validation) returns the install to setup mode, with the setup code shown only on the host terminal (host CLI, proof of presence at the host) and never delivered to Telegram; ordinary new-device approvals from Telegram (FR69) are unaffected. [§7, L3, L4, A11; AD-17, AD-37]
FR75: The Bridge is an installable PWA on the home-network origin, with a service worker, microphone access, passkeys and Web Push (on iOS through a home-screen install). [§7, §3.3, S7, B1; AD-13]

**Platform prerequisites (§8), stated as capabilities**

FR76: No back door: every Bridge and voice action passes the same consent, authority and audit as any other surface; there is no generic "run any command" endpoint; the audit records the owner as actor with the device. [§2 P3, Q5, §7 flow, §8.2; AD-1, AD-17]
FR77: Every web and voice action becomes a task in the existing task loop, never a second engine. [§2 P4, §8.2; AD-1, AD-26]
FR78 (★): A live, typed, persisted event stream covers tool and model calls, task claim and finish, job runs, consent, health, heals, memory writes, delegation and delivery; it is emitted at the action site with trace ids into a bounded replayable table, carried core→gateway and resumable by the browser; it also repairs split-mode TUI progress and the broken `publish` bus call. [§8.1]
FR79 (★): An authorised control path: every command has a severity, mutations are enqueued as tasks, and the web actor is recorded in audit. [§8.2]
FR80 (★): Consent never auto-grants an unknown channel: a channel with no registered prompter is denied and opens an incident; `web` and `voice` have real prompters. [§8.3; AD-18]
FR81 (★): The consent address (`reply_target`) survives the gateway↔core link. [§8.4; AD-18]
FR82 (★): The Bridge web server survives core restarts. [§8.6; AD-8]
FR83: A core query path returns in-memory state: active grants, live health, breakers, context windows and worker occupancy. [§8.8; AD-10]
FR84 (★): A time axis keeps health, cost, job and task history, including finished rows. [§8.9; AD-2]
FR85: A `web` conversation channel owns the response stream and renders action buttons. [§8.10; AD-34]
FR86: A streaming voice path runs mic → streaming STT → pipeline → streaming TTS, with spoken progress, speech that pauses when the owner talks and is then dropped or resumed, and typed stop and steer chosen by Owl's understanding. [§8.11, Q40, Q47, Q50; AD-32]
FR87: The current dashboard (`control_plane`) keeps the Q29 / L1–L4 login fix until the Bridge ships, and is deleted in the change that ships the Bridge, with no redirect or notice left at the old address. [§2 P9, Q29, Q45; AD-7]

### NonFunctional Requirements

**Performance and latency**

NFR1: The non-spoken acknowledgement (thinking mark plus acknowledgement sound) lands ≤ 300 ms after end of turn is detected, on every hardware tier. [Q39, §9 S1; AD-32]
NFR2: End of speech to first audio, measured with a stub LLM then the real pipeline: GPU and Apple Silicon P50 ≤ 800 ms and P95 ≤ 1.5 s; CPU-only and Jetson Orin P50 ≤ 2.5 s. GPU/Mac P50 > 1.2 s is a failure. Planning estimates for a no-tool turn: 0.6–1.2 s GPU, 0.8–1.5 s Apple Silicon, 1.5–4 s CPU/Jetson. [§5 tier table, §9 S1; [03] §6.3]
NFR3: The spoken "on it" arrives in under 1 s on GPU and Mac tiers. [Q39]
NFR4: StackOwl's own turn: first answer token ≤ 700 ms for chit-chat, and first progress ≤ 1 s for tool turns, over 30 voice prompts. If chit-chat exceeds 2 s, a separate fast "on it" path is added; thinking is never disabled to meet a budget. [§9 S2; Spine S2; [03] summary 3]
NFR5: Speech-to-text word error rate ≤ 8% on GPU tiers and ≤ 12% on CPU tiers over 100 owner phrases; zero hallucinated text on 20 silence and noise clips; streaming partial transcripts ≤ 300 ms. [§9 S3; [03] S3]
NFR6: TTS first audio ≤ 250 ms on GPU, ≤ 600 ms on Mac and ≤ 1 s on CPU, with an engine in the owner's blind top two. [§9 S4]
NFR7: Barge-in: Owl's speech pauses ≤ 300 ms after the owner starts speaking in at least 90% of cases; at most one false barge-in per 10 minutes of Owl speech (cue audio included); no earpiece routing on iOS. [§9 S5; Spine S5; [03] S5]
NFR8: End-of-turn detection on hesitant speech: premature cut-offs ≤ 5% and detection delay ≤ 400 ms (the acknowledgement clock starts after it). [§9 S6]
NFR9: An installed phone PWA holds a 10-minute voice session with no permission re-prompt and announces pause and resume. [§9 S7]
NFR10: The Viewscreen holds its frame budget for 30 minutes of recorded events on a mid-range Android and an older iPhone (including iOS Low Power Mode), without throttling. It renders on demand, caps the ambient tick at 15–30 fps, uses full rate only for event transitions and draws nothing while hidden; tier thresholds come from spike B3. [§6 Rendering, §9 B3; AD-20; Spine UI: rendering]
NFR11: Staleness is shown within one heartbeat timeout of a dead stream, and revocation takes effect within one heartbeat interval on every carrier. [§9 B2; AD-12, AD-16]

**Reliability and self-healing**

NFR12: The Bridge web server survives every core `os.execv` restart; durable reads (snapshot, journal catch-up, SQLite and md records) and command enqueue keep working while core restarts, and core-only values show as unavailable. [§8.6; AD-8, AD-10, AD-4]
NFR13: Resuming from the journal cursor replays the gap without loss or duplicates on both WebTransport and the SSE fallback; a cursor below retention gets `resync` and re-snapshots; fan-out never stalls on a cursor gap. [§9 B2, §11.2; AD-9, AD-11, AD-31]
NFR14: When WebTransport is unavailable, the stream falls back automatically to fetch-streamed SSE plus POST on the same authenticated origin; it never downgrades to plain HTTP and never bypasses a TLS error. [§9 B2; AD-11]
NFR15: Every event is recorded in the same transaction as the change it records: the Bridge never shows a rolled-back change and never misses a committed one. [AD-24]
NFR16: Needs-you items and their waiters survive core restarts; core re-materialises every open item's waiter from durable state. [AD-28]
NFR17: Nothing fails silently: the journal recorder, bridge server and notifier register health contributors, with healers wherever recoverable; the voice worker is supervised and restarted on crash, and a remote worker is health-checked; errors are logged, then self-healed or propagated; API refusals carry `{code, reason, remedy}`. [§2, §8; AD-23, AD-34; Spine conventions]
NFR18: Gateway and core prove compatibility at Hello (protocol version, migration number, event-registry digest); a mismatch refuses the link loudly, the older side restarts under supervision, and repeated failure opens an incident while the Bridge shows stale. [AD-33]
NFR19: Hardware degradation is never silent: the probe picks the correct tier on each host, or falls back to push-to-talk with the reason stated. [§9 S8; AD-22]
NFR20: One seeded backup set covers identity, the device key registry, VAPID keys, the Bridge server certificate and key, the journal and snapshot checkpoints; secrets are encrypted at rest; a restore keeps the install host name so passkeys and device CA trust survive. [AD-39]

**Security**

NFR21: Every request and stream carries a device-bound bearer token plus a proof-of-possession signature (method, path, timestamp, server nonce, body hash); the gateway stores only token hashes; no cookie is ever used; tokens last about 30 days of inactivity with a 90-day hard maximum; after rotation the previous token is accepted for at most 60 s, and later presentation counts as reuse. [§7, A4; AD-16]
NFR22: Every Bridge response carries the strict CSP with Trusted Types (`default-src 'none'; script-src 'self'; … require-trusted-types-for 'script'`), `nosniff`, `no-referrer`, `Cross-Origin-Opener-Policy: same-origin` and a Permissions-Policy granting the microphone to self only; record content is served sandboxed as an attachment; the service worker script is `no-store` with a versioned kill switch; the policy is never relaxed to make the build pass. [§7, A4; AD-36]
NFR23: No CA signing key exists after setup; the server certificate is ECDSA P-256 with the install host name as its only SAN, valid ≤ 825 days, key in the platform secret store; WebTransport certificates are ECDSA P-256, valid ≤ 14 days, keys only in gateway memory, hashes delivered only over the authenticated HTTPS snapshot. [A2; AD-14]
NFR24: Consent is decided once, core-side, and fails closed; always-ask categories (`prompt_surface`, `destructive`, `lock`, `alarm`, `authority_widening`, `owl_build`) are never bypassed by "run at once"; the provenance auto-grant never applies to `web` or `voice`. [§8.3; AD-18]
NFR25: Network exposure: IPv4 bind accepting only loopback and the host's directly attached private subnets (others refused with a logged remedy); no plain-HTTP listener; a request by IP or any other Host redirects to the install name and never shows a passkey prompt; a pinned allowlist of unauthenticated routes; QUIC address validation, connection caps and request-size caps; 0-RTT disabled; WebTransport checks `Origin` before auth and closes a session whose auth has not arrived within 5 s or exceeds 4 KB. [A1; AD-11, AD-13, AD-38]
NFR26: Brute-force protection covers setup-code, recovery-code and passkey attempts and device requests, per device and globally (never per IP only), refusing for a set period past the global budget and opening a high-intensity Needs-you item; setup codes are accepted only from the home network; the recovery code carries ≥ 128 bits and is stored as a slow hash. [AD-37]
NFR27: Auth invariants carried over from control_plane, for HTTP handlers and WebTransport sessions alike: fail closed without a credential; `Origin` present and checked before the token on browser routes; auth inside each handler, never middleware; a uniform 401; no token or proof in logs; no credential ever in a URL. [§7; AD-11, AD-16; [02] hard fact 8]
NFR28: The voice worker is never a principal: its token is scoped to transcribing and speaking; a remote worker uses mutual TLS; every voice session needs a per-session ticket bound to the device session; transcripts enter as requester kind `voice-unverified`; media uses host candidates only, with no third-party STUN or TURN. [AD-23]
NFR29: A push endpoint must be `https` and is refused when it resolves to a loopback, private or link-local address. [AD-19]
NFR30: Gateway, core and a local voice worker run as one OS user with the stated residual risk; no new secret may grant owner authority if stolen by a same-user process unless a binding mitigation applies; identity, requester-kind, standing-authority and session rows are writable only through `authz/` APIs that are never exposed as agent tools. [AD-27, AD-41]
NFR31: Security events (sign-in, signed taps, device request/approval/revocation, passkey add/remove, recovery-code use, token reuse, worker token issue/revocation, standing-authority grants) are also written to the hash-chained `audit_log` as evidence. [AD-17]
NFR32: The gateway↔core socket directory is owner-only (0700, or a named-pipe ACL on Windows); the gateway checks peer credentials and a per-boot link secret at Hello. [AD-33]
NFR49: A Telegram approval is accepted only from the owner's allowlisted Telegram chat, through the one resolver, bound to the item version and command digest shown in the message (a changed request is refused and re-shown), and a new-device approval also shows the device name and matching code; the residual risks are stated for every epic that touches approvals: a hijacked Telegram account can approve irreversible actions, and with home-network access could enrol a device and gain full Bridge control. [A8, A11; AD-18, AD-27, AD-28, AD-37]

**Privacy**

NFR33: The journal is metadata only: no message text, memory content, prompts, secrets, display names or exception text; actor and target ids are opaque; `attrs` hold ids, numbers, closed enums and labels of at most 64 characters; a runtime leak guard redacts secret-like strings and records that it did, proven by canary secrets driven through real emitters. [§3.4, §8.1; AD-4]
NFR34: Captions and presence are never persisted in the journal; the service-worker cache and Web Push payloads hold metadata only; no token, proof, secret or content appears in logs. [A5; AD-19, AD-30, AD-32; Spine conventions]
NFR35: Self-hosted: no feature requires a third-party service, with Web Push as the only named exception; browser cloud speech APIs are banned; the voice tier probe never selects a cloud backend (cloud TTS only by an explicit owner setting that shows its egress); nothing loads from a CDN or third-party host. [§2 P7, Q43, A2; AD-21, AD-22, AD-25]

**Licences and integrity**

NFR36 (superseded 2026-09-14, C35: licence rules dropped): Everything bundled or downloaded at runtime is MIT, Apache or BSD for code, may also be CC-BY for model weights, and may also be SIL OFL for fonts; an allow-list manifest declares every artifact and a tripwire fails anything undeclared or outside the set. No GPL or non-commercial component ships: Piper (GPL-3), python-zeroconf (LGPL-2.1+) and `pywebpush`/`py-vapid` (MPL-2.0) are excluded, as are XTTS-v2 (CPML), F5-TTS weights (NC), openWakeWord stock models (NC-SA) and the LiveKit turn detector. [§2 P6, Q25, Q44, A6; AD-25; [03] §5]
NFR37: Every runtime download (weights, voices, engines, the NVIDIA opt-in) is pinned by an immutable revision URL and SHA-256 and verified before an atomic rename; weights are accepted only as safetensors, ONNX or GGUF, never pickle; runtime package installs are exact-pinned with hashes. [AD-25]
NFR38: Every front-end asset, fonts included, is vendored and served by the platform; a fresh clone gets pre-built assets and needs neither Node nor a network. [Q43; AD-21]

**Portability and hardware**

NFR39: Runs on Linux, macOS and Windows on x86_64 and aarch64; defaults target a fresh clone on any host, never this dev box; host tiers use one shared capability probe; no vendor names in `src/`; no hardcoded keyword lists; all runtime state under `~/.stackowl/` via `StackowlHome`. [§2 P8; Spine conventions]
NFR40: Capabilities ship enabled; a setting may only opt out. [Spine conventions: config]

**Accessibility**

NFR41: Every drawn entity has a DOM/ARIA twin that is also its keyboard path; no text lives only in a canvas. [§2 P5; [01] §6.5]
NFR42: Moving content has a visible pause control (WCAG 2.2.2); `prefers-reduced-motion` is honoured (WCAG 2.3.3); nothing flashes more than three times per second (WCAG 2.3.1). [§2 P5, §6; [01] §6.5]
NFR43: Keyboard focus is always visible; the page never scrolls horizontally; text is never clipped; labels never overlap. [IB §6; HD §5]

**Retention**

NFR44: Journal retention is a setting with a 30-day default (provisional until spike B2), pruned only by one seeded job in bounded batches with `secure_delete` on and a WAL checkpoint after; it never deletes an event referenced by an unresolved Needs-you item and keeps the newest snapshot checkpoint at or before the oldest retained cursor. [§3.4, §8.1; AD-6, AD-35]
NFR45: Rows referenced by events are kept at least as long as journal retention, and completed COMMAND rows at least as long as the undo window (until superseded or 24 hours, FR88) and journal retention; older history stays readable because event evolution is additive with upcasters. [A9; AD-3, AD-4, AD-26, AD-27]

**Budgets for small hardware**

NFR46: Fan-out is bounded: a maximum number of carriers per device and in total, a bounded outbound queue per client with `resync` on overflow, and recording is never blocked by a slow client. [AD-38]
NFR47: Each turn and command has a journal write budget (events per `trace_id`, p95 `journal.record` overhead); exceeding it logs a WARNING and degrades the journal health contributor, but never drops events. [AD-38]
NFR48: The gateway has a memory budget sized for a Jetson-class host that also runs the agents (for example 7.4 GiB shared RAM), watched by the bridge health contributor; budget values come from spike B2. [AD-38; [02] hard fact 13]

### Additional Requirements

**Brownfield: no starter template.** The architecture specifies no starter or greenfield template. StackOwl is an existing platform, so there is no "Epic 1 Story 1: set up from a starter". The build starts with the spike gates and the §8 platform prerequisites below.

- **SPIKE GATES COME FIRST (Spine "Spike gates"; full-picture §9).** Voice spikes run on a GPU Linux box, an M-series Mac, a CPU-only laptop and a separate Jetson Orin, and B-spikes run on stock devices on a home network, never on the dev box, recording P50/P95. Each spike gates the ADs listed, and its fail branch is binding:
  - **B1** (fresh clone; stock iPhone iOS 26.4+/27 tab and installed app, Android Chrome 148+, desktop Chrome: `.local` name via OS mDNS, CA fingerprint trust, HTTPS, passkey on the local name, WebTransport `serverCertificateHashes`, setup code, second-device approval with matching code, mic, service worker, push, away-from-home summary, PWA install, a Safari tab idle 7 days, renewal ceremony) gates AD-13, AD-14, AD-16, AD-17, AD-19, AD-37. **Fail:** if B1 fails, the owner decides the path at that time (no preset fallback), given the failing step and device class.
  - **B2** (aioquic WebTransport interop with Safari 26.4+/iOS 27 and Chrome vs the fetch-SSE fallback; resume at real event rates with a burst and backgrounding on home Wi-Fi; leader-tab hand-off; slow-client resync; `journal.record` overhead and per-turn volume; gateway memory on the Jetson; 0-RTT refused; no Local Network Access prompt on Chrome 147+) gates AD-6, AD-9, AD-11, AD-12, AD-31, AD-35, AD-38. **Fail:** aioquic interop failure means SSE-only until a maintained WebTransport stack is chosen; a client class with silent gaps uses SSE + POST; retention default and budgets are lowered until memory stays bounded.
  - **B3** (Viewscreen frame budget, 30 min, mid-range Android and older iPhone incl. Low Power Mode) gates AD-20. **Fail:** that device class defaults to the low-power 2D mode.
  - **B4** (every event type mapped to one visual, replayed over a real recorded day) gates AD-3, AD-20 and the rendering convention. **Fail:** an event type without a truthful visual appears only in the narrated log and 2D views, never as an invented mover.
  - **B5** (committed build runs under the AD-36 CSP with Trusted Types on every B1 browser) gates AD-36. **Fail:** the failing construct is removed from the build; the policy is never relaxed.
  - **S1** (non-spoken acknowledgement and first audio per tier, stub then real pipeline) gates AD-22, AD-23, AD-32. **Fail:** Pipecat is not adopted and the worker drives the `media/stt`/`media/tts` selectors directly; a tier over its bound runs push-to-talk with the reason stated.
  - **S2** (first answer token and first progress over 30 voice prompts) gates AD-32. **Fail:** a separate fast "on it" path is added.
  - **S3** (STT accuracy on owner phrases and noise) gates AD-22, AD-25. **Fail:** that tier uses the best engine within the bound; with none, that tier is push-to-talk only.
  - **S4** (TTS first audio and owner's blind ranking) gates AD-22, AD-25. **Fail:** the default voice set uses the best passing engine, NVIDIA models included on NVIDIA hardware.
  - **S5** (barge-in and echo on iPhone, Android, laptop incl. cue audio; WebRTC vs WebSocket) gates AD-23, AD-32 and the sound convention. **Fail:** push-to-talk only on that tier.
  - **S6** (end of turn on hesitant utterances) gates AD-32. **Fail:** push-to-talk only on that tier until a turn detector passes.
  - **S7** (installed PWA permissions, lock, app switch, headset, close-and-reopen with a working mic; re-run each major iOS release) gates AD-23, AD-31 and the sound convention. **Fail:** phone voice is foreground-only, with a tap to start each session.
  - **S8** (probe four hosts, then force-degrade) gates AD-22 and the hardware-tiering convention. **Fail:** the probe selects the lowest tier with the reason stated; push-to-talk remains; never a silent failure.
  - **S9** (speaking over Owl during speech, generation and a running tool, across all interruption kinds) gates AD-27, AD-32. **Fail:** barge-in only pauses speech; stop, steer and correction go through on-screen controls until S9 passes.
  - The optional wake-word and full-duplex spikes (S10, S11) are dropped (Q7, Q28). Values the spikes fill (tier thresholds, voice bounds, barge-in thresholds, fan-out and queue bounds, per-turn journal budget, gateway memory ceiling, checkpoint interval, WebTransport rotation overlap) are the only things left open; the rules are fixed.
- **Paradigm:** CQRS around the one task loop: a write lane of declared commands executed as COMMAND tasks, and a read lane of an append-only, metadata-only event journal streamed to one client store per browser. [Spine Design Paradigm]
- **Typed COMMAND task kind and command migration (AD-1, AD-26):** every state-changing action has exactly one `CommandSpec` in `commands/` (type, typed payload, severity READ/WRITE/CONSEQUENTIAL, reversibility, undo command). The Bridge API, voice, the slash parser and every mutating LLM tool (`tools/scheduling/cronjob.py`, `tools/meta/owl_build.py` and the rest) are migrated to call the one typed submit entry, which validates the payload, sets requester kind from authenticated ingress provenance, and enqueues a `DurableTask` of kind COMMAND with `command_id` (idempotency key), nonce and, for voice, `utterance_id`; a gateway enqueue sends a payload-free `tasks_enqueued` wake frame. Commands execute only in core through one path (severity → action-policy gate → consent → deterministic handler that calls the subsystem mutator); no model runs for a COMMAND; no dry-run or preview returns before the severity check; no generic "run any command" endpoint. The mutator writes `command_id` in its own transaction (re-execution is a no-op); a command awaiting a decision is parked and holds no worker; at least one worker slot is reserved for COMMAND tasks. Tripwires fail any call to a declared mutator from outside the COMMAND handler registry and any subsystem mutator instantiated in the gateway. `CommandRegistry.dispatch`'s missing authorisation is closed by this path.
- **Action-policy gate (AD-27):** one gate in `authz/` decides every command from reversibility and requester kind (owner, owl/crew, `voice-unverified`, autonomous); the owner's own reversible order runs at once with undo, except that a spoken owner order at CONSEQUENTIAL severity gets a read-back and then needs the signed tap or an explicit Telegram approval, even when reversible (severity outranks reversibility for orders and requests alike); an owl or crew request needs a read-back, after which a spoken yes (`voice-unverified` included) approves a request that is reversible and not consequential, and its card shows undo; severity wins over reversibility; irreversible actions, consequential approvals, owl-authority grants and new-device approvals require the signed on-screen tap in the Bridge or an explicit Telegram approval, a spoken yes after read-back approves only requests that are reversible and not consequential, and owl-authority grants never come through voice; Telegram buttons from the owner's allowlisted chat may answer any approval item from anywhere with the same read-back, the first answer wins against the strip, and the residual risks are stated (a hijacked Telegram account can approve irreversible actions, and with home-network access could enrol a device and gain full Bridge control); signed taps use the device's non-extractable key over a server-issued single-use nonce bound to the command digest (type, payload, target, Needs-you item id and version); read-backs are rendered deterministically by the narrator, never a model; attendance is defined once (live session with an authenticated person present; scheduler and autonomous runs never attend); standing authority lives in one `authz/` table written only by `authority.grant`/`authority.revoke` commands that need a signed tap or an explicit Telegram approval; session consent grants stay in memory and never satisfy the irreversible rule; undo stays available until the action is superseded (a later command changes the same target) or 24 hours pass, whichever comes first, and an undo after that is refused; take-over pauses the owl's mission and hands the owner its current plan to edit and resume, or cancel, never continuing silently; a Q52 correction resolves the utterance's reversible commands by `utterance_id` and undoes them first; a tripwire fails any tool that reaches requester-kind, standing-authority or session rows.
- **Consent (AD-18):** decided once in core after enqueue, composed with the action-policy gate into at most one Needs-you item per command task; prompters only render and answer; the prompter channel set derives from the live `ChannelRegistry`; the `RoutingPrompter` → `AutonomousPrompter` fallback is deleted; a channel with no prompter fails closed with one `incident` per channel; autonomous runs carry `autonomous:scheduler`; `ConsentRequest.channel` comes from ingress provenance only; `ConsentRequestFrame` carries `reply_target`; the voice prompter renders the read-back and a spoken yes resolves only a request that is reversible and not consequential; Telegram, the authenticated owner channel, may answer any approval item, new-device approvals included, from anywhere, with the same read-back text (the device name and matching code for a device), and the first answer wins against the strip; a tripwire runs every always-ask category through the run-at-once branch and expects a prompt.
- **Journal (AD-2, AD-3, AD-4, AD-24):** one append-only SQLite table created by migration, cursor `INTEGER PRIMARY KEY AUTOINCREMENT`, never updated, deleted only by the AD-6 prune; derived, never a source of truth; neither `audit_log` nor jsonl, and the EventBus is not a source; split-mode TUI progress reads the journal stream. Subsystems call `journal.record(TypedEvent)` at the action site in the caller's transaction (transactional outbox; push only after commit; md writes record immediately after and degrade the journal contributor on failure; `ephemeral_source` types record right after their action). For commands, the subsystem mutator is the only recorder of the domain event and takes a `CommandContext`; the handler records only `command.*` lifecycle events. One versioned registry declares each type once with its `attrs` model, single emitting process, record kind, attention class and resolving types; a closed actor/target kind list (incl. `device`, `voice_worker`, `autonomous`); additive-only evolution with upcasters kept until the last row of a version is pruned (tripwire against `MIN(cursor)`); a coverage tripwire diffs migrated tables against the registry (`unjournaled` with a reason allowed). `record_ref` is `{kind: sqlite|md|graph, locator}`, each kind with one typed, authority-checked reader supplied by its subsystem (SQLite and md in the gateway, graph through a core query frame); `bridge/` never reads a table generically; targets without a store gain tables by migration (incidents, finished-task history, per-turn decisions); md- and graph-backed targets never gain mirror tables; a tripwire fails a referenced record kind whose prune window is shorter than retention.
- **Attention policy (AD-5):** one pure policy in `journal/` classifies every event as `ambient`, or `needs_you` at `normal`/`high`; failures and heals in progress are `ambient`; only explicit give-up types recorded by the owner of the retry or heal loop are `needs_you` `high`; emitters and the browser never classify; strip, accent colour, both sound classes, briefing, notifications and proactive speech all read it; there is no `fault` class.
- **Retention (AD-6):** a setting (30-day default), one prune job seeded by the idempotent job seeder, bounded batches, `secure_delete`, WAL checkpoint; retiring an event writer deletes its code, registry type and rows in the same change.
- **Package boundaries (AD-7):** `journal/` (imports no subsystem, `bridge/` or `voice/`; names and records arrive through registered ports), `commands/` (depends only on `authz/` and `pipeline/durable`), `authz/` (severities, principal `may`, action policy, attendance, standing authority), `authz/identity/`, `bridge/` (imported by no package), `voice/`; every new module argues its home in a `PLACEMENT:` docstring and a new package's placement is decided by vote.
- **Relocate authority and identity into `authz/` BEFORE `control_plane` is deleted (AD-7):** control_plane's severities move to `authz/`; the setup-code consumer and the `login_guard` brake move to `authz/identity/` (the setup code stays in the platform secret store); the Q29 migration, the deliverer hook and the reachability probe and census references are repointed in the same move; control_plane settings move to a `bridge` settings section through an idempotent config migration. The password hash, its L2 import (`config/control_plane_password_migration.py`) and `reset-password` are deleted, not relocated. (Code check 2026-09-13: `control_plane/password.py` and `config/control_plane_password_migration.py` exist, so the L1–L4 fix has at least partly landed.)
- **`control_plane` is deleted only in the change that ships the Bridge (Q45, AD-7):** that change deletes control_plane with its registration and tests, `ICON_SVG` and its guard included; its auth invariants become `bridge/` tripwires (AD-16); its page-constant, no-framework and no-CDN guards retire with it (Q43); no redirect or notice stays at the old address. Until then control_plane stays, with the Q29 login fix.
- **Gateway placement (AD-8):** the Bridge web server runs inside the gateway as a supervised task and reaches core-held state only through AD-9 and AD-10.
- **Fan-out (AD-9):** core pushes each event after commit on the existing link; the gateway records its own action sites (sessions, device approvals, voice) through its DbPool into the same table; fan-out merges by cursor, drops duplicates, upcasts, skips permanent holes and never blocks on a gap; after a core restart or lost link it reads the journal from its last cursor before resuming; live events are never polled.
- **Core query path (AD-10):** typed request/response frames on the existing gateway↔core link, each response carrying `as_of_cursor`; no second socket or connector; journal catch-up, the snapshot, SQLite/md readers and command enqueue go through the gateway DbPool; the write lane's only core frames are `tasks_enqueued` and delivery of a Needs-you resolution to its waiter.
- **Carriers (AD-11):** WebTransport over HTTP/3 with AD-14 certificate hashes; fallback to `fetch`-streamed SSE plus HTTPS POST on the same origin (never `EventSource`; tripwire against credentials in query strings); both carry one envelope family and resume from the same cursor; aioquic is provisional until B2.
- **Heartbeat (AD-12):** a server hello after auth announces heartbeat interval, core-offline grace, `bridge_api_version` and `build_id`; heartbeats carry `core_link` (`up`/`restarting`/`down`), `core_last_seen_at` and `head_cursor`; they are not journal rows; one interval is the revocation deadline.
- **One origin (AD-13):** one install host name under `.local` advertised by the OS's mDNS responder, or a bundled mDNS library such as python-zeroconf (C35) is the only origin for pages, API, SSE, WebTransport, WebRTC signalling, push registration, PWA and passkey RP ID; one port (HTTPS/TCP via aiohttp for pages, API and SSE; UDP via aioquic for WebTransport only); no `Alt-Svc`; bind, port and install name are `bridge` settings.
- **Ephemeral CA (AD-14):** as NFR23 and FR63–FR65; a seeded job opens the certificate-expiry Needs-you item 30 days ahead.
- **AD-15 is RETIRED** (home network only; no WireGuard); no work item.
- **Device-bound tokens (AD-16):** WebCrypto ECDSA P-256 non-extractable device key; token in IndexedDB with `navigator.storage.persist()`; an installed PWA and a browser tab are separate devices; single-flight rotation in the client store; revocation closes carriers, voice sessions and push subscription.
- **Owner principal (AD-17):** `authz/identity/` holds in SQLite the owner record, passkeys (py_webauthn), device key registry, device sessions, recovery-code hash and push subscriptions; secrets (setup code, server certificate key, VAPID key) stay in the platform secret store; every `web:<device>` and `voice:<device>` handle resolves in code to the owner's tenancy principal, `identity_key` and `ControlPrincipal` through `IdentityResolver` (no per-device alias in `stackowl.yaml`; tripwire that a web ingress and the Telegram owner resolve to the same `identity_key`); journal/audit actor is the owner with `device_id`; WebAuthn fixed RP ID, exact origin, `userVerification=required`, backup flags stored.
- **Notifier (AD-19):** one gateway notifier owns Telegram and Web Push dispatch, driven by fan-out of `needs_you.opened`/`needs_you.resolved`; standard VAPID with encrypted payload built on `http-ece` plus a VAPID signer over `cryptography`; a subscription row belongs to its device session and is deleted on revocation.
- **Front-end stack (AD-20):** Svelte for stations, panels and the strip; Three.js `three/webgpu` (`WebGPURenderer` with automatic WebGL2 fallback) for the Viewscreen only; TypeScript 6 type-checked by `svelte-check`; Vite; render tier from measured frame work time vs observed rAF cadence and sustained dropped frames plus `prefers-reduced-motion`, never battery APIs.
- **Committed build and drift test (AD-21):** source in `web/bridge/` with committed lockfile and pinned Node; built, minified assets committed to `src/stackowl/bridge/static/` and shipped as package data; `vite build` writes `bridge/static/build-manifest.json` (content hash of source and lockfile plus `build_id`); a Python tripwire recomputes the hash without Node; a CI job rebuilds with `npm ci --ignore-scripts` on the pinned Node and byte-compares `bridge/static/`; after a merge the bundle is rebuilt, never merged; the gateway serves the build loaded at start, and a client whose `build_id` differs forces a service-worker update and reload.
- **Voice channel and worker (AD-22, AD-23):** a gateway voice channel adapter joins the one conversation; speech engines run in a separate voice worker process (optionally on another home machine) chosen through the existing `media/stt` and `media/tts` selectors; the worker never connects to `core.sock`; the gateway supervises it with a `SupervisedTask`; worker tokens scoped, rotatable, revocable; per-session voice tickets minted at WebRTC signalling on the Bridge origin (a transcript frame without a live ticket is dropped and opens an `incident`); host candidates only; iOS audio only after a gesture; hidden page ends the voice session and delivers the unspoken reply as text; Pipecat, transport and tiering provisional until S1, S5, S9.
- **Download integrity (AD-25):** hash-pinned downloader with atomic rename; safetensors/ONNX/GGUF only. The licence allow-list and its tripwire were dropped (C35).
- **Needs-you rows (AD-28):** one migration-created table (`id`, `kind`, `intensity`, `record_ref`, `dedupe_key`, `waiter_kind`/`waiter_id`, `expires_at`, `version`, `opened_cursor`, `resolved_cursor`, `answer`, `resolved_by`); one resolver `needs_you.resolve` with a conditional update in the same transaction as the `needs_you.resolved` event; answers are not COMMAND tasks; partial unique index on `dedupe_key`; the answer on the row is authoritative.
- **Snapshot (AD-29):** one snapshot query over authenticated HTTPS through the gateway DbPool returns owls, channels, subsystems, memory kinds, jobs with next due time, the ordered open Needs-you set, last activity per owl, its consistent cursor and the current and next WebTransport certificate hashes; clients apply only later events as upserts or invalidations keyed by target, never increments; the front end never hardcodes these lists; `sensitive` config redacted (tripwire).
- **Narrator (AD-30):** one narrator in `journal/` renders every registered event type and every command read-back at delivery time, with current names from registered `NameResolver` ports (tombstone names for retired targets), in `full` (Bridge, briefing, Telegram) and `public` (Web Push, lock screen) renderings; no surface stores its own sentence; a tripwire fails any event type without a narration.
- **Client store (AD-31):** one store per browser owns the stream, cursor, heartbeat, stale state and power tier; tabs share one stream through Web Locks leader election plus `BroadcastChannel`; components never open a stream.
- **Voice state machine (AD-32):** one server-side machine (idle, listening, thinking, speaking, interrupted, mic-live); presence and captions travel as `voice` messages, never journal rows; understanding results (stop, steer, correct, backchannel) map to typed commands submitted as `voice-unverified` with `utterance_id`.
- **Hello and frames (AD-33):** bidirectional Hello; unknown frame type logs WARNING, never skipped silently; the unwired `steer`, `stop`, `query_running` and `running_state` frames and `ProgressEventFrame` are deleted and replaced by typed frames, with the gateway receiver repointed to the journal stream in the same change; every new frame bumps `protocol_version`; new frames live in `ipc/frames.py`, typed with `extra=forbid`.
- **Envelope family (AD-34):** message kinds `journal` (resumable), `conversation` (not resumable), `voice` (ephemeral), `command_result` (accepted, refused, awaiting decision, done, failed, keyed by `command_id`), `heartbeat`, `resync`, `hello`; a command POST returns `{command_id, task_id, trace_id}` or `{code, reason, remedy}`.
- **Checkpoints (AD-35):** a seeded job writes periodic metadata-only snapshot checkpoints with their cursor into a migration-created table; replay starts at the newest checkpoint at or before the requested time; interval is a setting, provisional until B2.
- **CSP (AD-36):** header tripwire; lint tripwire banning `{@html}`, `innerHTML` and `insertAdjacentHTML` in `web/bridge/`; one named Trusted Types policy.
- **Enrolment and recovery (AD-37):** setup mode only while zero passkeys exist or after the identity store is damaged (bytes readable but failing schema or integrity validation; an unreadable store never enters setup mode), when the setup code is shown only on the host terminal and never delivered to Telegram; Telegram delivery of the setup code only on a first install where no owner record ever existed; pre-auth responses reveal setup state only inside the setup flow; a new device is approved from a signed-in Bridge device, with the recovery code, or from Telegram with the device name and matching code, with the residual risk stated.
- **Budgets (AD-38):** as NFR46–NFR48.
- **Backup (AD-39):** as NFR20; restore order secrets → identity → journal and checkpoints; WebTransport certificates excluded.
- **Logo source (AD-40):** once control_plane is deleted, `logo/stackowl-mark.svg` and `logo/stackowl-logo.svg` (both already present in the repo) are the single source of the official mark; `web/bridge/` imports the mark from them; built app icons and the inline mark are generated from them; a drift tripwire asserts every shipped copy draws the same path; the mark is never redesigned.
- **Process isolation (AD-41):** gateway, core and a local voice worker run as one OS user (owner decision; no separate gateway OS user); residual risk stated for every epic; binding mitigations: no online CA key, identity, requester-kind, standing-authority and session rows writable only through `authz` APIs never exposed as agent tools, and device-bound tokens.
- **Migrations only (Spine conventions: schema changes):** every new table (journal, snapshot checkpoints, Needs-you items, identity, standing authority, incidents, finished-task history, per-turn decisions) comes only from idempotent SQL migrations in `src/stackowl/db/migrations/`, with the migration number allocated at merge; the runtime DDL in `audit/logger.py` and `channels/telegram/callbacks.py` is not a precedent; config moves use idempotent config migrations.
- **Conventions:** dotted, lower-case, past-tense event types (`task.claimed`); event envelope `event_id, cursor, type, schema_version, occurred_at, actor_kind+actor_id, device_id, target_kind+target_id, outcome, attention+intensity, record_ref, attrs, trace_id, duration_ms?`; `outcome` ∈ ok, failed, healed, parked, pending, dead_lettered, expired; `event_id` and `command_id` are UUIDv7 text from one helper in `journal/` on `uuid-utils`; time is ISO-8601 UTC text (no REAL epoch timestamps in new tables); SQLite or md only, one copy of each fact; every path through `StackowlHome`; 4-point structured logging (entry, decision, step, exit) through `infra/observability`; invariant guards carry `@pytest.mark.tripwire` and run through `scripts/tripwires.sh`.
- **Stack (pinned):** Python ≥3.13; aiohttp 3.13.5; aioquic 1.3.0 (abi3 wheels; provisional until B2); webauthn (py_webauthn) 3.0.0; cryptography 48.0.0; http-ece 1.2.1; uuid-utils 1.0.0; svelte 5.57.0; svelte-check 4.7.6; @sveltejs/vite-plugin-svelte 7.3.0; three 0.186.0; @types/three 0.186.0; typescript 6.0.3; vite 8.3.0; Node 24 LTS (build time only; minimum 22.12.0).
- **Environments:** dev uses a `localhost` origin on the same code path with no CA, and the `web` channel is drivable by `scripts/dev_ingress.py`; CI runs the Node build with `npm ci --ignore-scripts`, the byte compare and the tripwires; spikes run on stock devices on a home network, not the dev box.
- **Deferred (not to be designed ad hoc by an epic):** spike-gated values; the visual grammar mapping event type → motion (starting from the Helm Dial v2 grammar, B4); Viewscreen sampling values; the exact event type list (each epic adds its types with an `attrs` model, record kind and narration); retention beyond the 30-day default; Pipecat version pin; per-tier STT/TTS engines and voice assignment when owls outnumber voices (voice epic); silent-mode detection and whether a notification can carry the Needs-you sound (front-end research); filtering Owl's own voice beyond echo-cancelled output (voice worker, S5/S9); storage of the per-owner "last looked" marker (briefing); order of the first control actions (epic sequencing).

### UX Design Requirements

*Source note: the two mockup briefs are the UX contract. Mockup-only mechanics are not requirements: the three-variant switcher and `?variant=`, the scenario menu, replay speeds 1×/10×/60×, the "MOCKUP · recorded sample" label, `SCRIPTED` marks, `data.js`, the Canvas-2D preference and the cdnjs/Google Fonts loading forced by the Artifact CSP, the fixed 4 s heartbeat period, and the "NOT STREAMED TODAY" placeholder label (replaced by UX-DR10's real unavailable states).*

**Design tokens and identity**

UX-DR1: Implement the colour tokens once, consumed by Svelte and the Three.js Viewscreen, in a single dark theme that paints `body` and every colour explicitly: `--hull #0b0e12` (ground, the logo's tile); `--deck #11161c` (raised panels and sheets, blue-biased neutral, never grey); `--rule #1f2730` (hairlines, grid, bezel ticks); `--cream #f4f1ea` (instrument light: primary text and ordinary-activity motion); `--cream-dim #8d897f` (secondary text, idle marks, recorded past); `--caution #ffb020` (the one accent, master-caution amber, only for `attention = needs_you`); `--caution-ink #1a1204` (text on amber); `--stale #5b6470` (a dead or stale stream: breathing stops, marks desaturate to it). [IB §2; §6 Identity; Q13, Q34; Spine UI: structure]
UX-DR2: Colour rules: ordinary activity (turns, tool calls, successful job runs and deliveries) moves in cream only; an unhealed failure uses the same amber at higher intensity (solid, slow pulse) and nothing else may use amber; a healed failure shows no colour, only a small cream "healed" glyph in its record; there are no green or red status colours anywhere; state is carried by form (solid, hollow, dashed, struck) plus the single amber. [IB §2; §2 P2, Q34; AD-5]
UX-DR3: Vendor and serve three SIL-OFL typefaces locally (never from Google Fonts; CSP `font-src 'self'`), each with a real fallback stack: Display **Big Shoulders Display** 600–800, uppercase, letter-spacing .08em, used sparingly for station names, ring labels and the few big readouts; Body **Atkinson Hyperlegible Next** 400/600 (fallback `"Atkinson Hyperlegible", system-ui, sans-serif`) for cockpit and low-vision legibility; Data **Martian Mono** 400/500 (fallback `ui-monospace, "SFMono-Regular", monospace`) with `font-variant-numeric: tabular-nums` for timestamps, counts, cron schedules, durations and clock numerals. [IB §2; A6; AD-21, AD-25, AD-36]
UX-DR4: Type scale in px: 11 / 13 / 15 / 18 / 24 / 36 / 56. Captions are 15 px Body; labels are 11 px Display uppercase; crew module names 13 px Body; doing-lines 11 px Data. [IB §2; HD §1]
UX-DR5: Copy is written from the owner's side of the screen in the owner's platform vocabulary (owl names, job names, cron schedules, "dead-lettered", provider tiers); no lorem, no Marvel copy; all text renders as text nodes. [IB §2; §6; AD-36]
UX-DR6: The owl mark is the logo geometry, never redesigned: path `M3.5 2H20.5A1.5 1.5 0 0 1 22 3.5V7.5A1.5 1.5 0 0 1 20.5 9H13.4L12 6.9L10.6 9H3.5A1.5 1.5 0 0 1 2 7.5V3.5A1.5 1.5 0 0 1 3.5 2Z M9 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z M15 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z M3.5 10.5H20.5A1.5 1.5 0 0 1 22 12V14A1.5 1.5 0 0 1 20.5 15.5H3.5A1.5 1.5 0 0 1 2 14V12A1.5 1.5 0 0 1 3.5 10.5Z M6.5 17H17.5A1.5 1.5 0 0 1 19 18.5V20.5A1.5 1.5 0 0 1 17.5 22H6.5A1.5 1.5 0 0 1 5 20.5V18.5A1.5 1.5 0 0 1 6.5 17Z` (viewBox `0 0 24 24`, `fill-rule="evenodd"`), imported from `logo/stackowl-mark.svg`; the three blocks (top block with eyes and beak notch, full bar, shorter bar) are drawn as separate subpaths of the same coordinates for per-block lighting; PWA icons are generated from the same source; the mark's stacked rounded blocks set the grid and panel language. [IB §2; §6; AD-40]

**Owl presence mark**

UX-DR7: The host presence mark (28 px in the header beside the host's display name, default "Owl", and full size at the Dial centre) is animated only by light, driven by the server voice and liveness state machine:
- **idle:** slow cream breathe on the server heartbeat;
- **listening:** eyes brighten with the owner's own mic level;
- **thinking:** the three blocks light in sequence, visibly different from listening, entered within ~300 ms of detected end of turn together with the acknowledgement sound;
- **speaking:** blocks modulate with Owl's output-audio envelope, collapse the instant the owner starts to speak (when audio is flushed, not when the server acknowledges), resume after a backchannel or noise, and end when the paused speech is dropped;
- **needs-you:** amber, at higher intensity (solid, slow pulse) for an unhealed failure, together with the Needs-you alert;
- **link stale:** static in `--stale`, no breathing, age of last event shown;
- **ship offline:** distinct from link stale, shown when `core_link` is not `up` past the grace.

[§6 Host presence, Q23, Q39, Q40, Q47, Q50, Q51; IB §2; HD §1.6; AD-12, AD-32; [01] §5.2]

**Needs-you strip**

UX-DR8: The Needs-you strip sits on the top edge, full width on desktop and sticky under the header on phone, on every screen. It holds open items as amber chips, ordered by intensity then time opened, with the high-intensity item brighter; when empty it is a single `--rule` hairline (dark cockpit). A chip opens its item card in the station sheet; a resolved item, including one answered in Telegram, leaves the strip on every device; an item whose version changed is re-shown rather than answered. [IB §3; §3.3; AD-28]
UX-DR9: Item cards per kind:
- **approval:** the narrator's deterministic read-back (for example channel, text, attachment), the same text the Telegram message carries; for a request from Owl or crew that is reversible and not consequential, a spoken "yes" after the read-back or the Confirm control approves it and the card then shows undo; for an irreversible action, a consequential request or spoken order (even when reversible) or an owl-authority grant, a Confirm control that produces a signed tap (the item may instead be answered in Telegram);
- **unhealed incident:** what failed, what Owl tried, why no heal held, with "Resume job" / "Keep paused" and undo where Owl paused it, while the undo window is open (FR88);
- **device:** the requesting device's name and short matching code, with a signed-tap approve (the item may instead be answered in Telegram, whose message shows the same name and code);
- **question:** the clarifying question with its answer control;
- **incident and alert:** narrated cause and a link to the record (for example budget or certificate expiry).

[IB §3; §1, §3.3, A7, A8, A9, A11; AD-27, AD-28, AD-37]

**Station sheet**

UX-DR10: The station sheet is one frame: a right-side sheet 420 px wide on desktop and a bottom sheet 88 vh tall on phone, opened either on a station (from the station access controls of UX-DR38) or directly on a record, which is what tapping any mark does (`openStation(name, recordId?)`). It serves the six stations (Comms, Crew, Missions, Engineering, Archives, Security) from the snapshot, the stream and registered record readers. It shows truthful unavailable states instead of placeholders: `expired` for a gone target, "unavailable while core restarts" for graph records and core-only values, and live-probe values labelled with their `as_of` point. [IB §3; §3.2; AD-4, AD-10, AD-29]

**Station access**

UX-DR38: Station access:
- desktop: the six station names (Comms, Crew, Missions, Engineering, Archives, Security) sit on the Helm Dial's outer rim as Display-font labels that are real buttons opening the station sheet on that station, placed by computed angle so they never overlap the bezel numerals, next-due labels or ring labels; the Crew panel and Ship's log sit beside the Dial (UX-DR12);
- phone: a bottom bar holds the six stations plus `Now` and `Log` (UX-DR24);
- everywhere: every mark also opens its own station record.

[§3 Postures, A10; HD §1, §5; NFR41, NFR43]

**Captions bar**

UX-DR11: The captions bar sits at the bottom and shows Owl's speech as live captions and the owner's own utterances (live, partial, then final) in `--cream-dim` italics. A cream mic-live dot shows whenever audio is captured; a visible hands-free paused state appears after backgrounding. Captions are ephemeral `voice` messages and are never persisted. [IB §3; §5; AD-32, AD-34]

**Helm Dial v2 (the Viewscreen)**

UX-DR12: Desktop layout has three columns: the **Crew** panel (left, 280 px), the **Dial** (centre, square, as large as fits; the Three.js Viewscreen) and the **Ship's log** (right, 340 px), with the Needs-you strip on top, captions at the bottom and the six station names on the Dial's outer rim (UX-DR38). Panels are Svelte DOM; only the Dial is WebGPU/WebGL2. [HD §1, A10; AD-20]
UX-DR13: Every Dial ring carries its own on-screen uppercase Display label on its own arc (`SCHEDULE`, `COMMS`, `CREW`, `MEMORY`, `ENGINEERING`), so no legend is needed. Ring and module labels are placed by computed angle with collision nudging and never overlap; owl modules are evenly spaced on their ring. [HD §1, §5]
UX-DR14: **SCHEDULE (outer bezel):**
- a 24-hour clock in local time with Data-font hour numerals and a live "now" hand;
- every live recurring job from the snapshot is a tick at each due time in the next 24 h (completed one-shots are not drawn);
- the next three due jobs are written as short labels beside the hand (`telegram_canary · 15:00`);
- a job run flares its tick and sends a thin light inward along a radius to the actor that handled it in ENGINEERING;
- a failed run's light is dashed and stops short.

[HD §1.1, §2; §3.1; AD-29]
UX-DR15: **COMMS (docking ports on the rim):**
- one labelled port per channel present in the snapshot (`telegram`, `cli`, `slack`, …) at fixed rim angles;
- an incoming turn is a packet travelling from port to host (centre), and its delivery a packet from host or owl back to the port;
- a failed delivery bounces back dashed and leaves a small hollow mark with a count on the port.

[HD §1.2, §2]
UX-DR16: **CREW (crew ring):**
- every owl in the snapshot is a module: a mini stacked-block glyph derived from the mark (three bars, never the full mark, which is reserved for the host), its display name, and a one-line doing text (`thinking…`, `tool: process`, `task claimed`, `resting · last active Wed 21:09`);
- module states are all cream, expressed by form:
  - resting: dim, hollow bars;
  - thinking: bars light in sequence on a model call;
  - using a tool: top bar solid, with a spoke;
  - waiting for consent: a small lock glyph;
  - needs-you: an amber ring, only when a Needs-you item names this owl;
- non-owl actors (`scheduler`, `loop`) are not crew and live in ENGINEERING.

[HD §1.3]
UX-DR17: **MEMORY (inner ring around the centre):**
- split into labelled arcs by the real memory kinds reported in the snapshot (for example lessons · reflections · facts), falling back to one arc when kinds are unknown, each with a live count;
- a memory write is a light particle flowing from the writing module into its arc; the arc segment brightens for 1.5 s and the count ticks.

[HD §1.4, §2; AD-29]
UX-DR18: **ENGINEERING (the lower 120° band between crew and memory):**
- labelled segments for the subsystems that actually appear as actors or targets (`scheduler`, `loop`, `db`, provider or model, `health`, `self-heal`, …), capped at about 8 with the rest grouped as `other`;
- a heal pulses its segment cream once and leaves a tiny `healed` tick for 30 s;
- an incident draws a hollow outline, amber only when a Needs-you item exists for it;
- a health change shows solid (ok) versus hollow or dashed (degraded);
- a budget alert ticks the `fuel` segment's gauge;
- a labelled `DEAD LETTER` tray at the band's edge shows a count.

[HD §1.5, §2]
UX-DR19: **HOST (centre):** the host owl as the full logo mark, animated by presence (UX-DR7) and by its own events (thinking on its model calls, speaking during captions), with its display name and its own doing-line underneath. [HD §1.6]
UX-DR20: **Motion grammar**, one distinct, learnable motion per event class, each ≤ 900 ms of travel followed by a 2 s fade trail, mapped onto the dotted registry types (final mapping proven by B4):
- turn: packet, port → host;
- delivery: packet, host/owl → port (failed: bounces back dashed);
- model call: the actor's bars light in sequence (no travel);
- tool call: a short straight spoke outward from the module with the tool name at its tip for 1.5 s, then return (failed: dashed, no return);
- delegation: a curved arc between two crew modules, or host → module;
- task: a small square token travels along the crew ring from `loop` to the owl that claimed it (dead-lettered: the token drops into the `DEAD LETTER` tray);
- job run: bezel tick flare plus inward radius light;
- memory write: particle, module → arc;
- heal, incident, health change, budget alert: the ENGINEERING behaviours of UX-DR18;
- consent: the module's lock glyph opens (granted) or stays shut for 3 s (denied).

An event type with no truthful visual appears only in the Ship's log and 2D views. [HD §2; Spine Deferred; B4 fail branch]
UX-DR21: **Burst handling:** cap concurrent travellers (about 24 in the approved brief) and aggregate the overflow into a `+N` pulse on the target, so the Dial never becomes noise; the view declares that it samples; final values come from B2/B4. [HD §2; §2 P1; Spine UI: rendering]

**Crew panel**

UX-DR22: The Crew panel has one real-button row per owl, host first: module glyph · display name · doing-line · time since last event (Data font, tabular). Active rows move to the top and briefly highlight on each event. Idle rows stay truthful (`resting · last active …`, or `no activity in the retained window`). Clicking a row opens the Crew station on that owl. [HD §3]

**Ship's log**

UX-DR23: The Ship's log shows events newest first, one narrator-rendered plain-English line per event, written from the owner's side:
- successes: `14:03  Friday → used tool process ✓`, `14:03  scheduler ran telegram_canary ✓`, `14:04  healed: database reconnected`, `14:06  memory: 1 lesson saved by Friday`;
- failures read as failures: `✕ delivery to telegram failed`;
- bursts of identical events are grouped (`scheduler ran health_sweep ×6`);
- every line is a button that opens its record;
- the log is capped at 200 lines (older history lives in Archives);
- the header shows `last event … ago` when stale.

[HD §3; AD-30]

**Phone layout**

UX-DR24: Phone posture applies at ≤ 700 px wide:
- the Dial sits on top (square, full width, ring labels shortened, next-due labels hidden);
- a bottom bar holds the six stations, which open the station sheet, plus `Now` and `Log`, which switch the area under the Dial between **Now** (the Crew panel rows) and **Log** (the Ship's log) (UX-DR38);
- the Needs-you strip is sticky under the header;
- Comms is one swipe away;
- the station sheet is the 88 vh bottom sheet;
- a notification tapped at home opens straight on its item.

[HD §4; IB §3; §3 Postures, Q38, A10]

**Stale and low-power states**

UX-DR25: **Stale state:**
- breathing stops, all travel freezes, the Dial dims to `--stale` and marks desaturate;
- a `last event 00:42 ago` readout ticks up (also in the log header);
- *link stale* (missed heartbeat) is visibly distinct from *ship offline* (core link not up past the grace);
- stale is evaluated on visibility and carrier events, not only on throttled timers;
- resuming from the cursor clears it.

[IB §2; HD §5; §2 P1, §6; AD-12, AD-31]
UX-DR26: **Low-power 2D mode:**
- entered on measured frame work time against the observed rAF cadence with sustained dropped frames, on `prefers-reduced-motion`, or from the owner's visible motion control (never from battery APIs);
- no continuous motion and no travel: targets update state as static changes and the log keeps streaming;
- same truth, less spectacle;
- a device class that fails B3 defaults to it.

[IB §2; HD §5; §6 Rendering; AD-20]

**Truthful motion**

UX-DR27: **Truthful motion rules:**
- every mover maps to a journal event or snapshot record and opens its own station record on tap or click (A10);
- idle breathing follows the server heartbeat, never a CSS or render loop;
- idle motion is low-amplitude and visibly different from event motion;
- event state transitions are short (≤ 600 ms), with Dial travel ≤ 900 ms per UX-DR20;
- motion and colour intensity scale with severity, never with activity volume;
- decoration is allowed only where it cannot be read as data;
- projected marks (jobs due soon) come from snapshot records;
- rendering is on demand, the ambient tick is 15–30 fps, full rate is used only during event transitions, and nothing is drawn while the page is hidden;
- each view declares how it samples;
- the client applies stream events as upserts keyed by target, never as increments.

[§2 P1, §6; IB §2, §6; [01] §4.3; AD-20, AD-29; Spine UI: rendering]

**DOM twins and accessibility**

UX-DR28: Every canvas entity (crew module, host, port, bezel tick, ENGINEERING segment, memory arc, `DEAD LETTER` tray, in-flight traveller) has a DOM/ARIA twin in a visually hidden list of current marks with real buttons, which is also the keyboard path. Crew rows and log lines are real buttons. Keyboard focus is visible, and a visible pause control stops moving content. [§2 P5; IB §6; HD §5; NFR41–NFR43]

**Sound classes**

UX-DR29: Sound design has three sounds:
- **Ambient cues:** very soft texture for ordinary events, on by default; never repeat or form a barrage; duck under Owl's voice; silent when backgrounded; have their own off switch.
- **Needs-you alert:** the only attention-grabbing sound, the sound twin of amber, played only for `needs_you` with intensity set by severity; ambient cues never compete with it.
- **Acknowledgement sound:** plays with the thinking switch on detected end of turn; it is feedback and sits outside both classes.

Both classes have volume control and respect silent mode where the page can detect it. While a voice session is open, cues and Owl's voice play through the voice peer connection so echo cancellation covers them and they never trigger barge-in. [§6 Sound, Q14, Q39, Q51; Spine UI: sound; S5]

**Interaction flows**

UX-DR30: **Briefing presentation:**
- the briefing text appears first, and each named item lights its Dial element and Ship's log line in step with narration;
- a single "tap to hear" gesture starts speech (or it plays at once when hands-free is on);
- the same one tap resumes hands-free after a backgrounded phone reopens.

[§1, §4, Q37]
UX-DR31: **Anticipation and undo card:**
- shows what Owl or the owner's order did, with Undo until the action is superseded (a later change to the same target) or 24 hours pass, whichever comes first, after which Undo is no longer offered;
- after a Q52 correction, shows the automatic undo applied, then the corrected action;
- the record of a scheduled, pre-authorised irreversible run is a tappable card.

[§4, §5, Q11, Q35, Q36, Q52, A9; FR88]
UX-DR32: **Voice session controls:**
- push-to-talk control and hands-free toggle;
- mic-live indicator;
- honest slow-tier warning in hands-free;
- a stated reason when voice falls back to push-to-talk;
- when S9 has not passed on a tier, on-screen stop, steer and correction controls.

[§5, Q27; AD-23; S9 fail branch]
UX-DR33: **Setup, sign-in and re-trust flow:**
- guided per-device steps for stock iPhone, Android and desktop Chrome;
- CA fingerprint comparison against the host terminal;
- setup-code entry;
- passkey creation on the local name;
- one-time recovery code display;
- the renewal re-trust ceremony that removes the previous CA;
- setup state revealed only inside the setup flow;
- a request by IP redirects to the name without a passkey prompt.

[§7, A2; AD-13, AD-14, AD-37; B1]
UX-DR34: **Device approval pair:**
- the requesting device shows its device name and a short matching code;
- the approving surface shows the same name and code: a signed-in Bridge screen with a signed-tap confirm, or the Telegram message with an explicit approval button;
- only one request is pending at a time, and it expires with its item.

[§7, Q41, A11; AD-37]
UX-DR35: **Flight recorder:**
- a time-lapse control (about 30 s for a chosen window), speed control and return to live;
- recorded past rendered in `--cream-dim`;
- "unknown before retention" shown before the oldest checkpoint.

[§1, §3.4; IB §2, §4; AD-35]
UX-DR36: **Away-from-home notification summary:** a service-worker page showing the item's cached metadata-only summary, an "open at home" message and a "continue in Telegram" link, never an error page. [A5; AD-19; S7]
UX-DR37: **Installed PWA:** standalone display, icons generated from `logo/`; a `build_id` mismatch with the server hello forces a service-worker update and reload. [§7; AD-21, AD-40]
UX-DR39: **Take-over flow:**
- taking over pauses the owl's mission and opens its current plan in the station sheet for the owner to edit;
- the owner then resumes the mission with the edited plan, or cancels it;
- until then the mission shows as paused and taken over, and it never continues silently.

[§3.2, Q21, A9; FR89]

### Extraction Notes (conflicts and resolutions)

Rule applied: A-decisions and the spine win over older text; the full picture wins over research reports; mockup mechanics are not requirements.

- **C1 Voice approvals. RESOLVED (owner decision 2026-09-13, A7).**
  - *Conflict:* Q49, §4 and §10 let a spoken "yes" count after read-back for a reversible request from Owl or crew. AD-27 said "irreversible actions and approvals from voice need an on-screen signed tap", and "an approval … need[s] read-back plus an on-screen signed tap".
  - *Resolution:* Q49 stands. A spoken "yes" after Owl's read-back approves a request from Owl or a crew owl that is reversible and not consequential (severity wins over reversibility), with undo shown on the card, and `voice-unverified` may give it. Irreversible actions, consequential approvals, owl-authority grants and new-device approvals require the signed on-screen tap in the Bridge or an explicit Telegram approval (C2, C26); a spoken yes after read-back approves only requests that are reversible and not consequential. AD-27 is amended to match; applied in FR18, FR35, FR36, FR41, UX-DR9 and the action-policy gate requirement.
- **C2 Telegram-mirrored approvals. RESOLVED (owner decision 2026-09-13, A8).**
  - *Conflict:* §3.3 mirrors approvals to Telegram, but AD-18 said items needing a signed tap resolve only on a Bridge device. Irreversible approvals could therefore be seen on Telegram but not answered there, including away from home.
  - *Resolution:* Telegram buttons, as the authenticated owner channel, may approve any request, irreversible and consequential ones included, from anywhere. The Telegram message carries the same read-back text, and the first answer wins against the Needs-you strip. Residual risk, stated: a hijacked Telegram account can approve irreversible actions. This supersedes Q16's on-screen tap for approvals answered in Telegram; new-device approvals follow C26. AD-18 and AD-27 are amended; applied in FR8, FR18, FR20, FR22, FR25, FR36, FR37, FR41, NFR49, UX-DR8, UX-DR9 and the consent and action-policy gate requirements.
- **C3 Low-power trigger.**
  - *Conflict:* §6 says "battery saver … drops to low-power"; AD-20 says never from battery APIs.
  - *Resolution:* a measured frame budget plus reduced motion plus a visible owner control (UX-DR26).
- **C4 Fonts.** IB loads them from Google Fonts; Q43, AD-21 and AD-36 forbid third-party hosts. Resolution: vendored under SIL OFL (A6), per UX-DR3.
- **C5 Heartbeat period.** IB fixes it at 4 s; AD-12 has it announced by the server and tuned by B2. Resolution: the server-announced value.
- **C6 Transition timing.** IB caps transitions at ≤ 600 ms; HD allows ≤ 900 ms of travel plus a 2 s trail. Resolution: HD for Dial travellers, 600 ms for state transitions (UX-DR27).
- **C7 Needs-you kinds.** The IB engine uses `approval|unhealed|device`; AD-28 uses approval, question, incident, alert, device. Resolution: AD-28, with an unhealed failure as an `incident` at `high`. That mapping is an extraction choice; confirm it in the journal epic.
- **C8 Event names.** HD uses `turn`, `job_run`, `heal`…; the spine convention is dotted past tense. Resolution: the grammar is mapped onto registry types, and B4 finalises it.
- **C9 Phone layout.** HD §4 (`Now | Log` under the Dial) omits Comms; Q38 puts Comms one swipe away. Resolution: both are kept (UX-DR24); A10 moves `Now` and `Log` into the phone bottom bar with the six stations (C10).
- **C10 Station navigation. RESOLVED (owner decision 2026-09-13, A10).**
  - *Conflict:* §3 has "six stations around" the Viewscreen, and v1 opened stations from rim arcs. HD v2 replaced v1 with three columns and does not say how the six stations are opened.
  - *Resolution:* on desktop the six station names sit on the Helm Dial's outer rim and open the station sheet, with the Crew panel and Ship's log beside the Dial; on the phone a bottom bar holds the six stations plus Now and Log; every mark also opens its own station record. Applied in FR1, FR2, FR4, UX-DR10, UX-DR12, UX-DR24, UX-DR27 and the new UX-DR38.
- **C11 Stale one-shot jobs. STALE (2026-09-13).**
  - *Conflict (as extracted):* §3.1 says show the ship as it is (136 completed one-shot jobs still `enabled`); HD does not draw them.
  - *Status:* the 136 completed-but-enabled one-shot jobs were deleted live by migration 0143 on 2026-09-12 (commit 6c741c0a), so this is not a Bridge concern and needs no platform fix. FR9 (every job listed as it is) and UX-DR14 (only live recurring jobs on the bezel) stand as general rules.
- **C12 Superseded reach and certificates.** Q9, Q26, the Q42/Q46 domain fallback, [03] D13 remote access with TURN, [03] §5.3 "private domain or tunnel", [02] gap 1 "TLS-terminating remote access" and [01] §6.6's `EventSource`/`Last-Event-ID` are all excluded. A1, A2, AD-11, AD-13 and AD-23 (host candidates only) govern.
- **C13 Research versus the full picture.** Nemotron streaming STT as a default ([03] Stack A) is excluded by Q44. [03] S5 "bot audio stops" and S9/D9 "barge-in cancels" become pause, not stop, per Q40/Q47. [03] "optional wake word on a desktop tab" is excluded by Q7. [01] "health as aviation red/amber/green" is excluded by Q34 and IB's "no green/red".
- **C14 Old dashboard invariants.** [02] "no framework/CDN/build step, token in `sessionStorage`" is replaced by Q43 and by AD-16 (IndexedDB, device-bound tokens).
- **C15 Damaged identity store. RESOLVED (aligned 2026-09-13).**
  - *Conflict:* L3 said a damaged record returns the install to setup-code mode (with Telegram delivery); AD-37 said a damaged store never re-enables Telegram enrolment.
  - *Resolution:* both now say the same thing. A damaged store returns the install to setup mode, and that setup code is shown only on the host terminal (`stackowl control-plane reset-password` until control_plane is deleted, then the host CLI), as proof of presence at the host, never delivered to Telegram. Ordinary new-device approvals from Telegram (A11) are unaffected (FR68, FR74; AD-17, AD-37). An unreadable store (backend I/O error, locked keyring, permission failure) never enters setup mode: sign-in is refused with a remedy (503) and retried; only a damaged store (bytes readable but failing schema or integrity validation) does. L4 `reset-password` is deleted with control_plane, not relocated.
- **C16 Web action order.** In §7 the endpoint checks severity and then enqueues; AD-1 enqueues and then checks severity in core, with no dry-run before severity. Resolution: the spine (FR13, FR76).
- **C17 Backgrounded voice.** §5 has hands-free "paused"; AD-23 has the session "ends and the unspoken reply [is] delivered as text". Resolution: combined in FR50 (the server session ends, the UI shows paused, and one tap starts a new session).
- **C18 Silent mode.** §6 requires sounds to respect silent mode; §11.9 and Spine Deferred say a page may not be able to detect it. Resolution: respect it where detectable (FR60, UX-DR29). This remains open front-end research.
- **C19 Settled open questions.** §11.3 (placement vote) and §11.5 (frame versus socket) are settled by AD-8 and AD-10 [ADOPTED].
- **C20 Spike B5.** AD-36 adds spike B5, which is absent from full-picture §9; it is included in the gate list.
- **C21 Undefined values. RESOLVED (owner decision 2026-09-13, A9).**
  - The undo-window length was not defined anywhere, though AD-26 and NFR45 depend on it. *Resolution:* undo stays available on an action's card until the action is superseded (a later change to the same target) or 24 hours pass, whichever comes first (new FR88; FR17, FR31, FR34, FR35, NFR45, UX-DR9, UX-DR31).
  - The semantics of "take over" in Missions (§3.2, Q21) were not defined; the spine only said it is a COMMAND type. *Resolution:* take over pauses the owl's mission and hands the owner its current plan to edit and resume, or to cancel; never silent continuation (new FR89 and UX-DR39; FR8, FR9).
  - AD-27 is amended with both.
- **C22 Speaking envelope.** IB uses a "caption-driven envelope" (the mockup has no audio); §6 uses Owl's output audio. Resolution: output audio (UX-DR7).
- **C23 Grants shown in Security.** §3.2 shows consent grants that live in memory only; AD-27 adds persisted standing authority. These are not in conflict: both are shown and distinguished (FR12).
- **C24 Separate OS user for the gateway. RESOLVED by AD-41.** The owner decided that gateway, core and a local voice worker run as the same OS user, with the residual risk stated and the AD-41 mitigations binding; the spine's Open Questions section is removed.
- **C25 Added from research.** WCAG 2.3.1 (≤ 3 flashes/s) comes from [01] §6.5, which principle 5 cites; it does not conflict with anything.
- **C26 New-device approval via Telegram. RESOLVED (owner decision 2026-09-13, A11).**
  - *Conflict:* Q41, Q48, §7 and AD-37 allowed new-device approval only from a signed-in Bridge device or with the recovery code, never from Telegram, while A8 let Telegram approve every other request.
  - *Resolution:* a new device may also be approved from Telegram, where the message shows the device name and the same short matching code displayed on the new device; this supersedes Q48's "never from Telegram". Residual risk, stated: a hijacked Telegram account plus home-network access could enrol a device and gain full Bridge control. One step-up list applies everywhere: irreversible actions, consequential approvals, owl-authority grants and new-device approvals require the signed on-screen tap in the Bridge or an explicit Telegram approval; a spoken yes after read-back approves only requests that are reversible and not consequential. AD-18, AD-19, AD-27 and AD-37 are amended; applied in FR18, FR20, FR22, FR35, FR36, FR41, FR69, NFR49, UX-DR9, UX-DR34 and the consent, action-policy gate and enrolment requirements.
- **C28 Declared-command placement. RESOLVED (owner placement vote, 2026-09-13).**
  - *Conflict:* AD-7 puts the `CommandSpec` table and submit entry in `commands/`, depending only on `authz/` and `pipeline/durable`, but `commands/` is already the slash-command package (52 modules) with far wider imports.
  - *Resolution:* the table and submit entry live in a new `commands/spec/` sub-package, and the dependency rule applies to `commands/spec/`, enforced by a tripwire. The slash commands stay in `commands/` as a surface that submits. AD-1, AD-7 and the naming convention are amended.
- **C29 Existing jobs and standing authority. RESOLVED (owner decision, 2026-09-13).**
  - *Conflict:* AD-27 allows unattended irreversible actions only under standing authority the owner explicitly set up. The install's 33 enabled jobs (maintenance, briefs, check-in, digest and 4 goal jobs) have none recorded.
  - *Resolution:* grandfather all of them. The Story 4.8 migration records standing authority for each existing job's declared irreversible command types (provenance `grandfathered`), and the idempotent seeder records authority for platform-seeded jobs (provenance `seeded`). Both write through the `authz/` grant API and record `authority.granted` events. AD-27 is amended.
- **C30 Severity relocation timing.** AD-7 relocates control_plane's severities to `authz/` before deletion. Epic 4's gate needs them, so they move in Story 4.1. The setup-code consumer and `login_guard` still move in Epic 5.
- **C31 Spike host. RESOLVED (owner decision, 2026-09-13).**
  - *Conflict:* full-picture §9 and the spine require spikes to run on stock devices and on four hosts that are not the dev box (a GPU Linux box, an M-series Mac, a CPU-only laptop, a separate Jetson Orin).
  - *Resolution:* everything runs on the platform's own box, as it will on a customer's clone. Kits are cloned on the platform's host, and the owner's stock phones still perform the device checks. Stories 1.6, 2.12 and 7.3 are amended.
- **C32 Voice spikes are a built-in check. RESOLVED (owner decision, 2026-09-13).**
  - S1, S3, S4 and S8 (Epic 12), and S2, S6 and S9 (Epic 13), ship as `stackowl voice check`, which every install runs on its own host to measure its tier and state why. S5 and S7 are a device check page in the Bridge. They are not throwaway kits.
  - The owner's run on this box is the reference verdict that sets the default engines, voices and thresholds. The fail branches still apply per host.
  - Speech models may run on this Jetson dev box (they are small); the standing rule against local LLMs on the Jetson is unchanged.
- **C33 Quiet hours for Needs-you alerts. RESOLVED (owner decision, 2026-09-13).** High-intensity items push and alert at once. Normal-intensity items reach Telegram silently, and their Web Push waits until quiet hours end (Story 11.2).
- **C34 Sound source. RESOLVED (owner decision, 2026-09-13).** The licence allow-list has no audio category and the repo holds no audio files. All Bridge sounds are synthesized in Web Audio, with no bundled or downloaded audio (Story 6.10). After C35 the licence reason no longer applies; synthesized sounds remain the owner's choice.
- **C35 Licence rules dropped. RESOLVED (owner decision, 2026-09-14).** "This is open source platform. No license", then "Drop licence rules everywhere". No licence manifest or allow-list tripwire exists. Piper, python-zeroconf, pywebpush and NVIDIA-licensed models are ordinary engineering choices. NFR36, FR56 and FR57 are superseded, and the licence parts of AD-13, AD-19 and AD-25 are amended. Kept for security: hash-pinned runtime downloads verified before atomic rename, and weights never as pickle (NFR37). Fact at decision time: `pyproject.toml` declares `license = MIT` and there is no LICENSE file; installed dependencies include LGPL (python-telegram-bot), MPL (certifi, orjson, pathspec, tqdm, tld) and GPL (piper-tts).
- **C27 Consequential spoken orders. RESOLVED (owner decision 2026-09-13, A12).**
  - *Conflict:* Q35 and FR34 let the owner's own reversible spoken order run at once, while the step-up list made consequential requests need the signed on-screen tap in the Bridge or an explicit Telegram approval even when reversible.
  - *Resolution:* severity outranks reversibility for orders and requests alike. The owner's own spoken order for a consequential action gets Owl's read-back and then requires the signed on-screen tap in the Bridge or an explicit Telegram approval, even when reversible; everyday reversible, non-consequential spoken orders still run at once with undo (Q35). AD-27 is amended; applied in FR18, FR34, FR41, UX-DR9 and the action-policy gate requirement.

### FR Coverage Map

FR1: Epic 7 — full Bridge on desktop (Viewscreen, stations on the Dial rim, Needs-you strip)
FR2: Epic 7 — phone posture (compact Viewscreen, strip, bottom bar)
FR3: Epic 7 — live ship on the Viewscreen
FR4: Epic 7 — every mark caused by a real event and opens its record
FR5: Epic 7 — a quiet or failing ship looks quiet or failing
FR6: Epic 6 — breathing of the owl mark follows the server heartbeat (Viewscreen breathing re-verified in Epic 7)
FR7: Epic 8 — Comms station on the `web` channel
FR8: Epic 9 — Crew station
FR9: Epic 9 — Missions station
FR10: Epic 10 — Engineering station
FR11: Epic 10 — Archives station
FR12: Epic 10 — Security station
FR13: Epic 9 — first control actions (order decided in Epic 9)
FR14: Epic 6 — one priority-sorted Needs-you strip on every screen
FR15: Epic 3 — what the Needs-you queue holds
FR16: Epic 3 — unhealed failure is a high-intensity item; healed never enters
FR17: Epic 6 — unhealed-failure card
FR18: Epic 6 — approval card with Owl's deterministic read-back
FR19: Epic 3 — answered once across every surface
FR20: Epic 3 — Telegram may approve any request from anywhere
FR21: Epic 11 — every item through both Telegram and Web Push
FR22: Epic 3 (Telegram delivery per kind), Epic 11 (Web Push delivery per kind)
FR23: Epic 3 (Telegram message edited on resolve), Epic 11 (push notification replaced)
FR24: Epic 11 — at home a push tap opens the item
FR25: Epic 11 — away from home a push tap shows the cached metadata-only summary
FR26: Epic 11 — metadata-only push payloads
FR27: Epic 10 — flight recorder replay
FR28: Epic 11 — briefing of what changed since last looked
FR29: Epic 13 — briefing spoken
FR30: Epic 8 — Owl is the host, renamable; crew speak only when addressed
FR31: Epic 4 — Owl acts only within granted authority, card with undo
FR32: Epic 4 — irreversible autonomous action only under standing authority
FR33: Epic 4 — every action declares reversibility and undo
FR34: Epic 4 — owner's own reversible order runs at once with undo
FR35: Epic 4 — requests need read-back before the answer counts
FR36: Epic 4 — severity wins over reversibility; one step-up list
FR37: Epic 4 — standing authority granted and revoked only by explicit command
FR38: Epic 12 (push-to-talk), Epic 13 (hands-free)
FR39: Epic 12 — mic-live indicator
FR40: Epic 12 — live transcripts auto-send
FR41: Epic 12 — what a spoken order may trigger on its own
FR42: Epic 12 — non-spoken acknowledgement within ~300 ms
FR43: Epic 12 — spoken "on it" and milestone-only speech
FR44: Epic 13 — Owl's speech pauses when the owner speaks
FR45: Epic 13 — the utterance is understood, never keyword-matched
FR46: Epic 13 — paused speech dropped or resumed
FR47: Epic 13 — corrections answered fresh
FR48: Epic 13 — misheard order automatically undone
FR49: Epic 13 — proactive speech
FR50: Epic 12 — backgrounded phone ends the voice session, reply delivered as text
FR51: Epic 12 — one server-side voice state machine
FR52: Epic 13 — Owl's own voice never triggers barge-in
FR53: Epic 12 — hardware probe and honest tiers
FR54: Epic 12 — speech on a separate home machine
FR55: Epic 12 — distinct voice per owl
FR56: Epic 12 — superseded by C35 (NVIDIA models are ordinary engine candidates)
FR57: Epic 12 — superseded by C35 (the voice check picks the default engine)
FR58: Epic 12 — TUI and Telegram voice capture keep working
FR59: Epic 6 — owl mark presence states
FR60: Epic 6 — two sound classes
FR61: Epic 7 — WebGPU with WebGL2 fallback, low-power 2D mode
FR62: Epic 5 — home network only, IPv4, HTTPS
FR63: Epic 5 — guided setup with ephemeral CA and local name
FR64: Epic 5 — certificate renewal and re-trust ceremony
FR65: Epic 5 — short-lived WebTransport certificates by fingerprint
FR66: Epic 5 — passkeys plus one-time recovery code
FR67: Epic 5 — first-setup code on terminal and Telegram
FR68: Epic 5 — host CLI setup code; setup mode rules
FR69: Epic 5 — new-device approval from a signed-in device or Telegram
FR70: Epic 5 — device-bound session tokens
FR71: Epic 5 — revocation
FR72: Epic 5 — recovery code shown once, consumed and replaced
FR73: Epic 5 — one owner record; web and voice resolve to it
FR74: Epic 5 — unreadable identity store never enters setup mode
FR75: Epic 5 — installable PWA on the home-network origin
FR76: Epic 4 — no back door
FR77: Epic 4 — every web and voice action is a task in the one loop
FR78: Epic 2 — live, typed, persisted event stream
FR79: Epic 4 — authorised control path with severity and audit
FR80: Epic 3 — consent never auto-grants an unknown channel
FR81: Epic 3 — consent address survives the gateway↔core link
FR82: Epic 2 — survives core restarts (Hello, fan-out catch-up); re-verified for the Bridge server in Epic 6
FR83: Epic 10 — core query path for in-memory state
FR84: Epic 2 — time axis keeps history including finished rows
FR85: Epic 8 — `web` conversation channel with action buttons
FR86: Epic 12 (streaming push-to-talk path), Epic 13 (pause-on-speech and resume)
FR87: Epic 13 — `control_plane` deleted in the change that ships the Bridge (owner decision 2026-09-13: the Bridge replaces today's dashboard totally at the end of the final epic)
FR88: Epic 4 — undo open until superseded or 24 hours
FR89: Epic 9 — take over

## Epic List

**Sequencing decisions (owner, 2026-09-13).**
- **Evidence first:**
  - Spike B1, spike B5 and the carrier half of spike B2 open the build as Epic 1.
  - The journal half of B2 closes Epic 2, measured against the real journal. Epic 2 uses provisional values until it reports, because the spine fixes the rules and the spikes only fill in values.
  - Spikes B3 and B4 open Epic 7, so B4 replays a real recorded day.
  - Spikes S1, S3, S4 and S8 open Epic 12; spikes S2, S5, S6, S7 and S9 open Epic 13.
- **Who runs spikes:** the loop builds each spike's throwaway test kit and checklist, then stops at a story marked for the owner. The owner runs it from a fresh clone on the platform's own host, using their stock devices for the device checks. Voice spikes are not kits: they are built into the platform as `stackowl voice check`, which every install runs on its own host, and the owner's run on this box is the reference verdict for the defaults (owner decisions, 2026-09-13). The pass/fail result and its binding fail branch are recorded before the work that depends on it starts.
- **When the Bridge ships:** the Bridge replaces today's dashboard completely at the end of the final epic, Epic 13.
  - `control_plane` stays exactly as it is until then, with the Q29 / L1–L4 fix and no interim changes.
  - It is deleted in that change, with its registration, tests and guards. No redirect or notice stays at the old address.
  - Its severities and setup code move into `authz/` earlier, in Epic 5.
- **Epic 6 is split:** the live Ship's log, Needs-you strip and Crew panel in plain DOM come first as Epic 6. The Helm Dial Viewscreen follows as Epic 7.

### Epic 1: Proof on your own devices
The owner sees, on their own iPhone, Android and desktop on the home network, whether each mechanism the Bridge depends on actually works:
- local name, CA trust and HTTPS;
- passkey, setup code and second-device approval;
- WebTransport certificate hashes against the fetch-streamed SSE fallback, resume and backgrounding, leader-tab hand-off, slow-client resync, 0-RTT refused, and no Local Network Access prompt;
- microphone, service worker, push and PWA install;
- the strict CSP with Trusted Types.

Every result is recorded with its fail branch applied before any Bridge code is built.
**FRs covered:** none directly. The epic gates:
- B1: AD-13, AD-14, AD-16, AD-17, AD-19, AD-37;
- the carrier half of B2: AD-9, AD-11, AD-12, AD-31 and AD-38's fan-out and slow-client bounds;
- B5: AD-36.

### Epic 2: The ship keeps a truthful log
Every action the platform takes is recorded as it happens, in the same transaction as the change, as metadata only, and pruned after the configured retention. Recording survives core restarts without loss or duplication. Gateway and core prove compatibility at Hello, and split-mode TUI progress reads the event stream.

The visible change is small: nothing is lost across a core restart, and history exists. The epic closes with the journal half of spike B2, run by the owner: `journal.record` overhead, per-turn event volume and gateway memory on the Jetson. That result fills AD-6, AD-35 and AD-38's per-turn budget and memory ceiling.
**FRs covered:** FR78, FR82, FR84

### Epic 3: Owl's questions are durable and answered once
Approvals, clarifying questions, incidents and failures Owl could not heal become durable Needs-you items. They survive restarts and reach the owner on Telegram with Owl's deterministic read-back. The first answer wins everywhere. Consent never auto-grants an unknown channel, and its reply address survives the gateway↔core link. Each item's waiter is typed by kind from the start, so Epic 4's parked commands plug in without rework.
**FRs covered:** FR15, FR16, FR19, FR20, FR22 (Telegram), FR23 (Telegram), FR80, FR81

### Epic 4: Every change goes through one door
Every state change the owner or an owl makes, from Telegram, TUI, a slash command or an LLM tool, is one declared, authorised, audited command. It carries a severity and reversibility and runs through the one task loop. Reversible actions can be undone until superseded or for 24 hours. Irreversible autonomous action happens only under standing authority the owner explicitly granted. The epic begins by moving the severities and principal from the old dashboard into `authz/`. The declared-command table lives in `commands/spec/`. Existing jobs keep their irreversible actions through grandfathered standing authority (owner decisions, 2026-09-13).
**FRs covered:** FR31, FR32, FR33, FR34, FR35, FR36, FR37, FR76, FR77, FR79, FR88

### Epic 5: Open the Bridge at home and sign in
The owner sets up the Bridge through guided setup with a private per-install CA and a local name. They install it as an app on their devices and sign in with a passkey plus a recovery code. New devices are approved from a signed-in device or Telegram, and any device can be revoked. The old dashboard's setup code and brute-force brake move into `authz/identity/` first (its severities already moved in Epic 4); `control_plane` itself keeps running unchanged.
**FRs covered:** FR62, FR63, FR64, FR65, FR66, FR67, FR68, FR69, FR70, FR71, FR72, FR73, FR74, FR75

### Epic 6: The ship's log and Needs-you strip, live in your browser
The owner opens the Bridge and sees lightweight screens with no 3D:
- the narrated Ship's log, newest first;
- the Crew panel, one row per owl with what it is doing;
- the owl presence mark breathing on the server heartbeat;
- the Needs-you strip on every screen.

Approvals and failures Owl could not heal are answered in place, with a signed tap where the step-up list requires one. The stream reaches the browser over WebTransport or the SSE fallback, from one snapshot and one client store. A stale or restarting core link shows as stale, and the two sound classes play.
**FRs covered:** FR6, FR14, FR17, FR18, FR59, FR60

### Epic 7: The Helm Dial Viewscreen
Spikes B3 and B4 run first; B4 replays a real recorded day from the journal. Then:
- the Helm Dial shows the live ship: owls and what they are doing, missions in flight, jobs approaching their due time, memory, engineering and comms;
- every mark is caused by a real event and opens its record;
- a quiet or failing ship looks quiet or failing;
- desktop shows the full bridge with station names on the Dial rim; the phone shows the compact Viewscreen with the bottom bar;
- rendering uses WebGPU with WebGL2 fallback and a low-power 2D mode.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR61

### Epic 8: Talk to Owl in Comms
The owner converses with Owl in the browser. It is the same conversation, memory and task loop as Telegram, with action buttons, and Owl is the ship's host, renamable.
**FRs covered:** FR7, FR30, FR85

### Epic 9: Crew and Missions: see and steer the work
The owner sees each owl's activity, authority, skills and DNA, and every task, schedule, retry and dead letter. They steer the work through the first control actions (pause, resume, retry, cancel, run now, grant, take over), each shown on a card with undo.
**FRs covered:** FR8, FR9, FR13, FR89

### Epic 10: Engineering, Archives and Security
The owner sees:
- health, providers and self-healing incidents over time;
- what the ship knows about them and the decision ledger;
- flight-recorder replay of any past window;
- active authority, approval history, devices and sign-ins.
**FRs covered:** FR10, FR11, FR12, FR27, FR83

### Epic 11: Alerts on your phone and the briefing
Every Needs-you item also arrives by Web Push, beside Telegram. At home a tap opens the item; away from home a tap shows a cached, metadata-only summary. When the owner opens the Bridge, a short briefing says what changed since they last looked.
**FRs covered:** FR21, FR22 (Web Push), FR23 (Web Push), FR24, FR25, FR26, FR28

### Epic 12: Talk to Owl — push-to-talk
The built-in `stackowl voice check` covers spikes S1, S3, S4 and S8, and the owner's reference run on this box sets the defaults. Then:
- the owner talks to Owl with push-to-talk on every device and hardware tier;
- a separate voice worker, optionally on another home machine, runs the speech;
- the platform states each tier and its cost honestly;
- each owl has a distinct voice;
- a non-spoken acknowledgement arrives within about 300 ms, and transcripts appear live;
- the voice check picks the default engine.
**FRs covered:** FR38 (push-to-talk), FR39, FR40, FR41, FR42, FR43, FR50, FR51, FR53, FR54, FR55, FR56, FR57, FR58, FR86 (streaming path)

### Epic 13: Hands-free conversation — and the Bridge replaces today's dashboard
The voice check grows to cover S2, S6 and S9, and a device check page in the Bridge covers S5 and S7 on the owner's phones. Then:
- hands-free conversation with an open mic;
- Owl's speech pauses when the owner talks, and Owl understands stop, steer, question or correction;
- misheard orders are undone automatically;
- proactive speech, and the briefing spoken.

The epic's last story ships the Bridge as the complete replacement for today's dashboard and deletes `control_plane` with its registration, tests and guards. That story depends only on Epics 1–12 and on the hands-free stories being finished, whether they passed their spikes or took their binding fail branches.
**FRs covered:** FR29, FR38 (hands-free), FR44, FR45, FR46, FR47, FR48, FR49, FR52, FR86 (pause and resume), FR87

## Epic 1: Proof on your own devices

The owner sees, on their own iPhone, Android and desktop on the home network, whether each mechanism the Bridge depends on actually works. Every result is recorded with its fail branch applied before any Bridge code is built. Gates: B1, B5 and the carrier half of B2.

**How this epic runs:**
- **Kit:** each story builds part of one throwaway test kit under `spikes/bridge/`. Its README and entry files state that it is throwaway. It declares its own dependencies inline, so none enter the platform lockfile.
- **Loop's check:** stories 1.1–1.5 are done when the kit passes its automated checks against Chromium on the build host (a CDP virtual authenticator for passkeys, a local push service).
- **Device runs:** every real-device run is owed in Story 1.6, which parks at `awaiting-operator`.
- **Where kits run:** from a fresh clone on the platform's own host on the home network, reached by the owner's stock devices (owner decision, 2026-09-13).

### Story 1.1: Trust the Bridge's own certificate on your phone at home

As the owner,
I want a throwaway kit I start from a fresh clone and open by its local name over HTTPS on my devices,
So that I know the private-CA and local-name approach works on real phones before any Bridge code exists.

**Acceptance Criteria:**

**Given** a fresh clone on a Linux or macOS host on the home network
**When** the owner runs the kit's single documented start command
**Then** it creates a CA key and signs one ECDSA P-256 server certificate carrying only the install host name as SAN with `id-kp-serverAuth`, valid for at most 825 days
**And** it destroys the CA key, so an automated check finds no CA private key on disk or in memory after setup (AD-14, NFR23)
**And** it prints the CA's SHA-256 fingerprint with guided trust steps for iOS, Android and desktop Chrome

**Given** the host's own mDNS responder (for example Avahi or Bonjour)
**When** the kit starts
**Then** the install name under `.local` is advertised through that responder's own tool, or the exact command is printed when the kit cannot run it

**Given** the kit is listening
**When** a request arrives by IP address or any Host other than the install name
**Then** it is redirected to the install name, and no passkey prompt can appear on that origin
**And** the listener binds IPv4 only, accepts only loopback and the host's directly attached private subnets, refuses any other source with a logged remedy, and has no plain-HTTP listener (NFR25)

**Given** a device that trusted the CA after the fingerprint comparison
**When** it opens `https://<install-name>.local:<port>`
**Then** the page loads with no certificate warning, and the page can be installed as a PWA with a registered service worker that opens standalone (FR75)

**Given** the owner runs the kit's renewal command
**When** renewal completes
**Then** a new CA fingerprint is shown and the guided steps remove the previous CA from each device before trusting the new one (FR64)

**Given** any kit page
**When** the owner marks a checklist step pass or fail with a note
**Then** the result is written to `spikes/bridge/results/B1-<device-class>.json`, together with the kit version, browser and OS version and a timestamp
**And** the automated check proves the certificate chain, the redirect, the subnet refusal and result writing against Chromium on the build host

### Story 1.2: Sign in with a passkey on the local name and approve a second device

As the owner,
I want to sign in with a passkey on the local name and approve a second device by matching its code,
So that I know enrolment, device-bound tokens and device approval work on my real devices.

**Acceptance Criteria:**

**Given** a first start with no enrolled passkey
**When** the kit issues a setup code
**Then** the code appears on the host terminal
**And** it is sent to the single allowed Telegram user when exactly one is configured, and shown on the terminal only when none or several are
**And** a setup code is accepted only from the home network, and no network request can mint one (AD-37, FR67, FR68)

**Given** the kit's Telegram steps (setup-code delivery and device approval buttons)
**When** the kit starts
**Then** it uses a separate test bot token given to the kit, never the live platform's bot token, because Telegram allows only one update consumer per bot and a second poller would knock the live platform's Telegram off (409 Conflict)
**And** the kit refuses to start, with a remedy, if its token matches the platform's configured bot token

**Given** a valid setup code on the install-name origin
**When** the owner creates a passkey
**Then** py_webauthn verifies it with the RP ID set to the install name, the exact expected origin and `userVerification=required`, and the backup-eligible and backup-state flags are recorded (AD-17)

**Given** successful sign-in
**When** the browser creates its device key
**Then** the key is a non-extractable WebCrypto ECDSA P-256 key, and the token is stored in IndexedDB after `navigator.storage.persist()` is called
**And** every kit request carries the bearer token plus a signature over method, path, timestamp, server nonce and body hash
**And** a copied token replayed without the key is refused with a uniform 401 (AD-16, NFR21, NFR27)

**Given** a signed-in first device and a new second device
**When** the second device requests access
**Then** it shows its device name and a short matching code, and the first device shows the same name and code
**And** approval with a signed tap on the first device enrols the second device
**And** a Telegram message to the allowed user shows the same name and code, and approving there also enrols the device
**And** only one device request can be pending at a time (AD-37, FR69)

**Given** Safari on iOS
**When** the signed-in tab and the installed app are left idle for 7 days
**Then** the checklist records whether the token survived without a new passkey sign-in, with start and end timestamps

**Given** the Chromium virtual authenticator
**When** the automated check runs
**Then** it covers setup-code refusal from outside the home network, passkey create and get, a copied token being refused, and the matching-code approval

### Story 1.3: Push, microphone and the away-from-home summary on your devices

As the owner,
I want notifications, the microphone and the away-from-home summary proven on my phones,
So that I know alerts reach me and a notification tap never dead-ends when I am away from home.

**Acceptance Criteria:**

**Given** a signed-in device (on iOS, the installed home-screen app)
**When** the kit sends a Web Push notification
**Then** it is delivered through standard VAPID with an encrypted payload (AD-19)
**And** the payload carries only an item id, kind, intensity and a short public rendering (FR26)

**Given** a push subscription request
**When** its endpoint is not `https`, or resolves to a loopback, private or link-local address
**Then** it is refused (NFR29)

**Given** a delivered notification
**When** the owner taps it on the home network
**Then** the kit opens that item's page

**Given** a delivered notification on a phone that is off the home network
**When** the owner taps it
**Then** the service worker shows the cached metadata-only summary with "open at home" and a link to Telegram, with no error page
**And** an automated check proves the service-worker cache holds no content beyond that metadata (FR25, UX-DR36)

**Given** the kit's microphone page in a browser tab and in the installed app
**When** the owner grants microphone access
**Then** a live level meter shows captured audio, and the checklist records whether the permission persists after closing and reopening

**Given** a local push service stand-in
**When** the automated check runs
**Then** it covers payload encryption and signing, the endpoint refusals and the offline summary page in Chromium

### Story 1.4: A live stream over WebTransport with automatic SSE fallback, on your phone

As the owner,
I want a real recorded event stream delivered to my phone over WebTransport, falling back to SSE by itself,
So that I know the live Bridge stream stays truthful through bursts, backgrounding and weak connections.

**Acceptance Criteria:**

**Given** the kit's HTTPS origin
**When** it starts its WebTransport server on the same port over UDP with aioquic 1.3.0
**Then** it uses a self-signed ECDSA P-256 certificate valid for at most 14 days, identified by `serverCertificateHashes`, with the current and next hashes delivered only over authenticated HTTPS
**And** 0-RTT early data is refused, `Origin` is checked before auth, and a session whose signed auth message is late by 5 s or larger than 4 KB is closed (AD-11, AD-14, NFR25)

**Given** a browser without WebTransport, blocked UDP, or a handshake that still fails after one hash refresh
**When** the client connects
**Then** it falls back automatically to `fetch`-streamed SSE plus HTTPS POST on the same origin, carrying the `Authorization` header and proof of possession
**And** `EventSource` is never used, and no credential ever appears in a URL (NFR14)

**Given** a recorded sequence of real platform events, sanitised to metadata and numbered with a cursor
**When** the replayer streams it at the recorded rates, including a burst, with heartbeats carrying `head_cursor`
**Then** a stream killed mid-flow shows as stale within one heartbeat timeout (NFR11)
**And** after backgrounding, app switches or a network drop, resume from the cursor replays the gap with no loss and no duplicates on both carriers, proven by comparing received cursors against sent cursors (NFR13)

**Given** two open tabs of the kit
**When** the leader tab closes
**Then** the other tab takes over the one stream through Web Locks plus `BroadcastChannel`, with no cursor gap (AD-31)

**Given** a deliberately throttled client
**When** its outbound queue overflows
**Then** that client receives `resync` and resumes from its cursor, and the replayer never blocks (NFR46)

**Given** each device run
**When** the run ends
**Then** connect time, resume replay time and kit server process memory are recorded as P50 and P95 in `spikes/bridge/results/B2-carrier-<device-class>.json`
**And** the checklist records whether Chrome 147+ showed a Local Network Access prompt, and whether WebTransport certificate hashes worked; the latter also counts toward B1

**Given** Chromium on the build host
**When** the automated check runs
**Then** it covers the WebTransport session, a forced SSE fallback, cursor resume with no loss or duplicates, leader hand-off and slow-client resync

### Story 1.5: The strict security policy holds on every browser

As the owner,
I want a minimal build on the pinned front-end stack running under the Bridge's exact security policy,
So that I know the real Bridge can be built without ever relaxing that policy.

**Acceptance Criteria:**

**Given** the pinned stack (Svelte 5.57.0, three 0.186.0 through `three/webgpu`, Vite 8.3.0, TypeScript 6.0.3 checked by svelte-check 4.7.6, Node 24 LTS or at least 22.12.0)
**When** the kit's front end is built
**Then** it renders the owl mark imported from `logo/stackowl-mark.svg` in a DOM view, and in a `WebGPURenderer` scene that falls back to WebGL 2 automatically (AD-20, AD-40)
**And** the built assets are committed inside the kit, so a fresh clone needs neither Node nor a network (NFR38)

**Given** any kit response
**When** it is served
**Then** it carries exactly the AD-36 header set: the full `Content-Security-Policy` with `require-trusted-types-for 'script'`, `nosniff`, `no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and a `Permissions-Policy` granting the microphone to self only
**And** exactly one named Trusted Types policy exists (NFR22)

**Given** the kit's front-end source
**When** a lint check runs
**Then** it fails on any `{@html}`, `innerHTML` or `insertAdjacentHTML`

**Given** the page is running
**When** any `securitypolicyviolation` event fires
**Then** the violation is recorded in `spikes/bridge/results/B5-<device-class>.json` with the blocked directive and source
**And** a pass requires zero violations plus a working render on that browser, and the policy is never edited to reach a pass

**Given** Chromium on the build host
**When** the automated check runs
**Then** it proves zero violations, the exact header set and the WebGL 2 fallback path

### Story 1.6: Verdicts decide what gets built

As the owner,
I want every Epic 1 result turned into a verdict per gate with its binding fail branch named,
So that the following epics are built on evidence from my devices, not assumptions.

**Acceptance Criteria:**

**Given** result files for B1, the carrier half of B2, and B5
**When** the owner runs the kit's verdict command
**Then** it scores each result against the full-picture §9 and spine pass criteria and writes `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md`, which lists for each spike and device class:
- the verdict;
- the failing steps;
- the ADs that are confirmed or stay provisional;
- the fail branch that applies.

**And** a device class with no result file is reported as "not run", never as a pass (gates AD-9, AD-11–AD-14, AD-16, AD-17, AD-19, AD-31, AD-36–AD-38)

**Given** a failing result
**When** the verdict is written
**Then** it names the binding fail branch:
- B1: the owner decides the path, given the failing step and device class;
- B2 carrier interop: the carrier becomes SSE-only until a maintained WebTransport stack is chosen;
- a client class that shows silent gaps: that class uses SSE plus POST;
- B5: the failing construct is removed from the build, and the policy stays unchanged.

**Given** the kit from Stories 1.1–1.5 plus the verdict command
**When** this story completes its agent work
**Then** the kit is preserved on a local `spike/bridge-epic-1` branch, which is not pushed unless the owner decides to, and `spikes/bridge/` is deleted from main in the same change, so no kit code remains on main
**And** the kit's README on that branch explains how to clone it over the home network from the build host

**Given** the agent work is committed
**When** the story finalises
**Then** its spec is set to `status: awaiting-operator`, and `operator_actions` lists:
1. Clone `spike/bridge-epic-1` into a fresh directory on the platform's own host on the home network.
2. Create a test bot in BotFather and give its token to the kit (never the live platform's bot token).
3. Run every checklist on a stock iPhone (iOS 26.4+ and iOS 27, Safari tab and installed app), Android Chrome 148+ and desktop Chrome.
4. Complete the 7-day Safari idle step.
5. Run the verdict command and commit `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md` to main.
6. Decide the path for any failed B1 step.
7. Run `bmad-loop confirm` for this story.

## Epic 2: The ship keeps a truthful log

Every action the platform takes is recorded as it happens, in the same transaction as the change, as metadata only, and pruned after the configured retention. Recording survives core restarts without loss or duplication, gateway and core prove compatibility at Hello, and split-mode TUI progress, which today receives nothing from core because no core code sends `ProgressEventFrame`, comes back from the journal stream. The epic closes with the journal half of spike B2.

### Story 2.1: The journal records task events in the same transaction as the change

As the owner,
I want every task state change recorded in one append-only journal inside the same transaction as the change,
So that what the platform shows about its work can never disagree with what actually happened.

**Acceptance Criteria:**

**Given** no journal exists
**When** the platform migrates
**Then** an idempotent SQL migration in `src/stackowl/db/migrations/` creates the journal table with the cursor as `INTEGER PRIMARY KEY AUTOINCREMENT` and the conventions envelope columns (`event_id`, `type`, `schema_version`, `occurred_at` as ISO-8601 UTC text, `actor_kind`, `actor_id`, `device_id`, `target_kind`, `target_id`, `outcome`, `attention`, `intensity`, `record_ref`, `attrs`, `trace_id`, `duration_ms`) (AD-2, FR78)
**And** the new `src/stackowl/journal/` package argues its home in a `PLACEMENT:` docstring and its placement is decided by vote (AD-7)

**Given** the event registry in `journal/`
**When** an event type is declared
**Then** it has one typed metadata-only `attrs` model, one emitting process, a record kind and an attention class, with actor and target kinds from one closed list that includes `device`, `voice_worker` and `autonomous`
**And** `journal.record` refuses an unregistered type or invalid `attrs` loudly (AD-3)

**Given** a subsystem changing state on its SQLite connection
**When** it calls `journal.record(conn, event)`
**Then** the event is inserted in that same transaction, so a rolled-back change leaves no event and a committed change always has one, proven by a test that forces a rollback after recording (AD-24, NFR15)
**And** `event_id` is UUIDv7 text from the one helper in `journal/` built on `uuid-utils`

**Given** any string value in an event
**When** it is recorded
**Then** the leak guard scans it with the log redactor and a secret-pattern detector, redacts matches and records that it redacted
**And** a test drives canary secrets through the real task emitters and finds none in the journal (AD-4, NFR33)

**Given** the durable task store
**When** a task is enqueued, claimed, finished or dead-lettered
**Then** `task.enqueued`, `task.claimed`, `task.finished` and `task.dead_lettered` are recorded at the action site with the task as target, an `outcome` from the closed set and a `record_ref` to the task row

**Given** the journal recorder
**When** a record call fails
**Then** the error is logged with 4-point logging, the journal health contributor reports degraded with a remedy in the health sweep, and the failure is never swallowed (NFR17)

### Story 2.2: Every event reads as a plain sentence and knows whether it needs the owner

As the owner,
I want every recorded event to render as one plain-English sentence and to be classified once as ambient or needing me,
So that every surface later tells me the same thing and only real give-ups ask for my attention.

**Acceptance Criteria:**

**Given** a registered event type
**When** the attention policy classifies it
**Then** the result is `ambient`, or `needs_you` with intensity `normal` or `high`, computed purely from its registered class with no I/O on the write path (AD-5)
**And** failure and heal-in-progress types are `ambient`, and only explicit give-up types recorded by the owner of the retry loop (such as `task.dead_lettered`) are `needs_you` at `high`

**Given** the codebase
**When** the tripwires run
**Then** they fail any emitter that sets `attention` or `intensity` itself, and any registered type without an attention class

**Given** a recorded event
**When** the narrator renders it at delivery time
**Then** it produces a `full` sentence and a `public` rendering (kind and count only), using current names from `NameResolver` ports that subsystems register into `journal/`, and `journal/` imports no subsystem (AD-30, AD-7)
**And** a target that no longer exists renders with its tombstone name, never an error

**Given** the registry
**When** the tripwires run
**Then** they fail any registered event type without a narration, and any surface that stores its own sentence for an event

### Story 2.3: Gateway and core refuse to talk across versions

As the owner,
I want gateway and core to prove at connect time that they run matching versions,
So that a half-upgraded install fails loudly instead of corrupting data or dropping messages silently.

**Acceptance Criteria:**

**Given** gateway and core connecting
**When** Hello is exchanged
**Then** it travels in both directions and carries `protocol_version`, the highest applied migration number and the event-registry digest (types, versions and attention policy version), and the new frames stay typed with `extra=forbid` in `ipc/frames.py` (AD-33, NFR18)

**Given** any mismatch in those three values
**When** the link is evaluated
**Then** it is refused with an ERROR log that names both values, and the side running the older version restarts under supervision
**And** before its first write after any link loss or mismatch, the gateway pauses its journal writes until the migration numbers match

**Given** repeated failed matches
**When** the supervision limit is reached
**Then** restarts stop and the health sweep reports the link unhealthy with a remedy (Epic 3 turns this into an `incident` Needs-you item)

**Given** a frame of unknown type
**When** either side receives it
**Then** it logs a WARNING naming the type and is never skipped silently

**Given** the unwired `SteerFrame`, `StopFrame`, `QueryRunningFrame` and `RunningStateFrame`
**When** this story lands
**Then** they are deleted with any references and tests, and `protocol_version` is bumped

### Story 2.4: Only the real core can connect to the gateway

As the owner,
I want the gateway↔core link to accept only the core the gateway itself started,
So that no other local process can impersonate core and inject events or answers.

**Acceptance Criteria:**

**Given** the platform starts on Linux or macOS
**When** the IPC socket directory is created
**Then** it is owner-only (0700), and on Windows the named pipe carries an owner-only ACL (NFR32)

**Given** a connection on the IPC socket
**When** Hello arrives
**Then** the gateway checks peer credentials against the core process it supervises, and verifies a per-boot link secret that was passed to core at exec (AD-33)
**And** a peer failing either check is refused with a logged remedy, and the secret never appears in logs

**Given** a core `os.execv` restart
**When** the new core connects
**Then** it presents the secret for this boot and the link is accepted

### Story 2.5: Split-mode TUI progress comes back, from the journal

As the owner,
I want the split-mode TUI to show live progress from the recorded event stream,
So that I see what the platform is doing again, and nothing is lost when core restarts.

**Acceptance Criteria:**

**Given** core records an event
**When** its transaction commits
**Then** core pushes the event to the gateway as a typed journal frame on the existing link, and never before commit (AD-9, AD-24)

**Given** the gateway receives core pushes and records its own action sites
**When** fan-out delivers events
**Then** it merges both streams by cursor, drops duplicates, upcasts rows and skips a cursor at or below the committed maximum that is absent on read as a permanent hole, never blocking on it (AD-9)

**Given** a core `os.execv` restart or a lost link
**When** the link returns
**Then** the gateway reads the journal from its last delivered cursor before it resumes live fan-out, and a test across a real restart shows no lost or duplicated events (NFR13)

**Given** `ProgressEventFrame` and its gateway receiver
**When** this story lands
**Then** the frame is deleted, the receiver is repointed to the journal stream in the same change, and `protocol_version` is bumped (AD-33)
**And** split-mode TUI progress (pipeline step changes and similar progress events) renders from journal events, driven end to end through `scripts/dev_ingress.py`
**And** the journal is never polled for live events
**And** fan-out and catch-up readers use short read transactions and never hold one open across a stream, so WAL checkpoints can complete (AD-38)

### Story 2.6: Jobs, heals and health have a history

As the owner,
I want job runs, self-healing and health changes recorded over time,
So that the platform can show what ran, what failed, what healed and what gave up, not just how things look right now.

**Acceptance Criteria:**

**Given** the scheduler runs a job
**When** a run starts, finishes, fails or is parked
**Then** the matching `job.*` events are recorded at the action site with a `record_ref` to the job or job-run row, and `job.parked` is recorded by the scheduler that stops retrying (AD-5)

**Given** a healer attempts a heal
**When** it attempts, succeeds or stops trying
**Then** `heal.attempted`, `heal.healed` and `heal.exhausted` are recorded by the heal loop's owner, with `heal.exhausted` classified as `needs_you` `high` and the others as `ambient`

**Given** the health sweep
**When** a subsystem's health changes
**Then** a `health.changed` event records the subsystem, the previous and new status and an error code, with no exception text (FR84)

**Given** a finished task, finished job run or health change
**When** its event's `record_ref` is opened within journal retention
**Then** the row is still present
**And** a tripwire fails any task, job or job-run prune window shorter than journal retention (AD-4, NFR45)

### Story 2.7: Every turn's model calls, tool calls and delegation hops are recorded

As the owner,
I want each turn's model calls, tool calls and delegation hops recorded as metadata,
So that I can later see how Owl and the crew actually worked on a request without any of its content being stored.

**Acceptance Criteria:**

**Given** a turn runs through the pipeline
**When** it makes a model call, a tool call or a delegation hop
**Then** the corresponding events are recorded with `trace_id`, `duration_ms`, outcome, error code, provider or tool identifier as a bounded label, and the owl as actor (AD-2)
**And** no prompt, message text, tool argument or result content is recorded, proven by canary strings driven through a real turn (NFR33)

**Given** a turn that records per-turn decisions
**When** its event is recorded
**Then** its `record_ref` opens the `turn_decisions` row

**Given** a real turn driven by `scripts/dev_ingress.py` with only the AI provider mocked
**When** the turn completes
**Then** the journal holds its model, tool and delegation events in cursor order under one `trace_id`

### Story 2.8: Memory writes and consent decisions are recorded

As the owner,
I want every memory write and consent decision recorded,
So that what the ship learns and what it was allowed to do are both on the record.

**Acceptance Criteria:**

**Given** a curated md memory write
**When** the write completes
**Then** a `memory.*` event is recorded immediately after it, with an `md` `record_ref` to the file and section anchor under `StackowlHome`
**And** a failed record marks the journal health contributor degraded (AD-24)

**Given** a SQLite memory write or a consent decision
**When** it happens
**Then** its event is recorded in the same transaction, with outcome codes only

**Given** each event type added in this story
**When** the tripwires run
**Then** every one has an `attrs` model, a record kind, an attention class and a narration

### Story 2.9: Deliveries, channels and providers are recorded

As the owner,
I want message deliveries, channel activity and provider changes recorded, including actions taken in the gateway process,
So that the log is complete across every subsystem and both processes.

**Acceptance Criteria:**

**Given** a delivery attempt or a provider state change
**When** it happens
**Then** its event is recorded in the same transaction, with outcome codes only

**Given** channel ingress handled in the gateway
**When** a message arrives
**Then** the gateway records `channel.*` metadata events through its own DbPool into the same journal, and fan-out delivers them merged by cursor with core events (AD-9)

**Given** each event type added in this story
**When** the tripwires run
**Then** every one has an `attrs` model, a record kind, an attention class and a narration

### Story 2.10: Nothing escapes the journal

As the owner,
I want the platform to prove that every table is either journaled or explicitly excused, and that every recorded target can be opened,
So that the log never quietly misses a part of the ship.

**Acceptance Criteria:**

**Given** the migrated table set
**When** the coverage tripwire runs
**Then** every migration-created table is listed in the registry with its event types, or marked `unjournaled` with a written reason, and the tripwire diffs the migrated set against the registry (AD-3)

**Given** an event type with more than one `schema_version`
**When** the upcaster tripwire runs
**Then** it fails if the model or upcaster of any version whose rows still exist (checked against `MIN(cursor)` for that version) has been removed

**Given** each record kind (`sqlite`, `md`)
**When** a reader is registered
**Then** it is supplied by the owning subsystem, typed and authority-checked, runs in the gateway (md read-only), and `bridge/` never reads a table generically (AD-4)
**And** a target that no longer exists opens as an `expired` record, never an error

**Given** a new migration adds a table without registry coverage
**When** the tripwires run
**Then** the coverage tripwire fails and names the table

### Story 2.11: The journal stays small on small hardware

As the owner,
I want the journal pruned on a schedule and bounded in how much each turn writes,
So that recording everything never fills the disk or slows a small host.

**Acceptance Criteria:**

**Given** the `journal` settings section
**When** no value is set
**Then** retention defaults to 30 days, and a setting may change it (AD-6, NFR44)

**Given** the scheduler's existing idempotent job seeding
**When** the platform boots
**Then** exactly one journal prune job exists, and repeated boots never create a second one

**Given** the prune job runs
**When** events are older than retention
**Then** it deletes them in bounded batches with `secure_delete` on and checkpoints the WAL afterwards, and nothing else deletes journal rows
**And** it logs each checkpoint's result (busy, log and checkpointed pages), and the journal health contributor degrades with a remedy when the WAL file stays above its size budget
**And** it never deletes an event held by a registered retention hold (later epics register holds, for example unresolved Needs-you items)

**Given** a turn or command that exceeds its journal write budget (events per `trace_id`, p95 `journal.record` overhead)
**When** the budget is crossed
**Then** a WARNING is logged and the journal health contributor degrades, and no event is ever dropped (AD-38, NFR47)
**And** the budget values are provisional settings until spike B2 reports

### Story 2.12: Journal cost measured on a small host

As the owner,
I want the journal's real cost measured on the platform's own host,
So that retention and budget values come from evidence, not guesses.

**Acceptance Criteria:**

**Given** a throwaway benchmark under `spikes/bridge-journal/` (marked throwaway, dependencies declared inline)
**When** it drives real turns and a burst through `scripts/dev_ingress.py` on a running platform
**Then** it records P50 and P95 `journal.record` overhead, events per `trace_id` for each turn kind, and gateway memory before, during and after the burst, in a results file

**Given** a results file
**When** the owner runs the verdict command
**Then** it writes `docs/agentic-os-dashboard/spikes/epic-2-b2-journal-verdict.md` with the measurements, a verdict per B2 journal criterion (including bounded gateway memory) and the resulting values for the per-turn write budget, the p95 overhead budget, the gateway memory budget and the retention default
**And** when memory is not bounded, the verdict names the binding fail branch: lower the retention default and the budgets until memory stays bounded (AD-6, AD-35, AD-38, NFR47, NFR48)

**Given** a verdict with values
**When** it is written
**Then** the verdict command appends one entry to `_bmad-output/implementation-artifacts/deferred-work.md` naming the settings defaults to change, so `bmad-loop sweep` applies them

**Given** the benchmark is complete
**When** this story finishes its agent work
**Then** the benchmark is preserved on a local `spike/bridge-epic-2` branch and deleted from main in the same change

**Given** the agent work is committed
**When** the story finalises
**Then** its spec is set to `status: awaiting-operator`, and `operator_actions` lists:
1. Clone `spike/bridge-epic-2` into a fresh directory on the platform's own host.
2. Run the benchmark and the verdict command.
3. Commit the verdict document and the deferred-work entry to main.
4. Run `bmad-loop confirm` for this story.

## Epic 3: Owl's questions are durable and answered once

Approvals, clarifying questions, incidents and failures Owl could not heal become durable Needs-you items. They survive restarts, reach the owner on Telegram with Owl's deterministic read-back, and are answered once, with the first answer winning everywhere. Consent never auto-grants an unknown channel, and its reply address survives the gateway↔core link.

Until Epic 4, a consent prompt or question waits inside the turn that asked, in core memory, so a core restart resolves it as `expired` instead of leaving it hanging.

### Story 3.1: Give-ups become durable Needs-you items

As the owner,
I want every failure Owl could not heal, every budget alert and every broken gateway↔core link to become a durable Needs-you item,
So that what needs me is never lost on a restart and appears exactly once per problem.

**Acceptance Criteria:**

**Given** no Needs-you table exists
**When** the platform migrates
**Then** an idempotent migration creates the table owned by `journal/` with `id`, `kind` (`approval`, `question`, `incident`, `alert`, `device`), `intensity`, `record_ref`, `dedupe_key`, `waiter_kind`, `waiter_id`, `expires_at`, `version`, `opened_cursor`, `resolved_cursor`, `answer` and `resolved_by`
**And** a partial unique index on `dedupe_key` over unresolved rows keeps one open item per target (AD-28)

**Given** an event the attention policy classifies as `needs_you`
**When** it is recorded
**Then** an item opens in the same transaction with its intensity set only by the policy, and a `needs_you.opened` event is recorded
**And** a second `needs_you` event for the same kind and target while the item is open opens no duplicate

**Given** `heal.exhausted`, `task.dead_lettered` or `job.parked`
**When** it is recorded
**Then** an `incident` item opens at `high`, while `heal.attempted` and `heal.healed` never open an item (FR16)

**Given** the cost tracker's budget warning, or the repeated Hello mismatch from Story 2.3
**When** it happens
**Then** an `alert` item, or an `incident` item, opens respectively (FR15)

**Given** the registry declares resolving types for an opening type (for example, a job resumed after `job.parked`)
**When** a resolving event is recorded
**Then** the recorder closes the open item through the one resolver

**Given** unresolved items
**When** the open set is requested
**Then** `journal/` returns every unresolved item ordered by intensity, then `opened_cursor`
**And** the Story 2.11 prune job never deletes an event referenced by an unresolved item, because each open item is registered as a retention hold

### Story 3.2: One answer wins, on every surface

As the owner,
I want every Needs-you item resolved by one atomic resolver,
So that answering on two surfaces at once can never approve something twice or disagree about what I decided.

**Acceptance Criteria:**

**Given** an open item
**When** `needs_you.resolve` is called
**Then** it runs `UPDATE … WHERE id = ? AND resolved_cursor IS NULL` in the same transaction as the `needs_you.resolved` event, and the answer on the row is authoritative (AD-28)

**Given** two answers to the same item arriving concurrently
**When** both call the resolver
**Then** exactly one wins, and the other receives the winning outcome, proven by a concurrency test (FR19)

**Given** a resolved or expired item
**When** another answer arrives
**Then** the resolver returns the winning outcome and changes nothing

**Given** an answer carrying an item version or digest different from the current item
**When** it reaches the resolver
**Then** it is refused, and the item is re-shown with its current version

**Given** an item whose `expires_at` has passed
**When** the seeded expiry sweep runs, or any surface reads or answers the item
**Then** the item resolves as `expired` through the same resolver, whether its waiter is an in-memory turn, a durable command task, or none (alerts and incidents)
**And** exactly one expiry sweep job exists, seeded idempotently, so no expired item stays open in the strip

**Given** a winning resolution
**When** it commits
**Then** only that resolution is delivered to the item's waiter (by frame when the waiter is in core), and no answer is ever enqueued as a COMMAND task

### Story 3.3: Approvals and questions become Needs-you items

As the owner,
I want every consent request and clarifying question to become a Needs-you item answered through the one resolver,
So that approvals and questions follow the same once-only, never-lost rules on every channel.

**Acceptance Criteria:**

**Given** a turn needs consent for a tool
**When** the consent request is raised
**Then** an `approval` item opens, bound to its waiter with `waiter_kind` and `waiter_id`, and its expiry follows the consent timeout (AD-28)

**Given** a turn issues `clarify_ask`
**When** the question is raised
**Then** a `question` item opens, bound to that turn's waiter (FR15)

**Given** the Telegram, Slack, Discord, WhatsApp and TTY prompters
**When** the owner answers on any of them
**Then** the answer goes only through `needs_you.resolve`, and the waiting turn receives only the winning answer

**Given** an open item whose waiter lives in a turn's memory
**When** core restarts and that turn is gone
**Then** core resolves the item as `expired` through the resolver at boot, and the item is never left open with nothing waiting
**And** `waiter_kind` distinguishes in-memory turn waiters from durable waiters, so parked COMMAND tasks (Epic 4) re-materialise instead of expiring

**Given** a real turn driven through `scripts/dev_ingress.py`, with only the AI provider mocked
**When** a consent request is answered in Telegram
**Then** the item resolves once, the turn continues with that answer, and `needs_you.opened` and `needs_you.resolved` are in the journal

### Story 3.4: Consent never grants a channel nobody can answer

As the owner,
I want a consent request on a channel with no prompter to be refused and reported to me, while scheduled work keeps its explicit autonomous grants,
So that nothing is ever approved on my behalf by accident, and unattended jobs still finish.

**Acceptance Criteria:**

**Given** the consent routing
**When** the platform starts
**Then** the prompter channel set derives from the live `ChannelRegistry` (the adapters that started), never from a hardcoded list (AD-18)

**Given** a consent request on a channel with no registered prompter
**When** it is decided
**Then** it is denied, and one `incident` item opens for that channel (deduplicated per channel), so the owner is told (FR80, NFR24)
**And** the `RoutingPrompter` fallback to `AutonomousPrompter` is deleted

**Given** a scheduled or autonomous run
**When** its trigger starts it
**Then** it carries the explicit principal `autonomous:scheduler`, set by the trigger and never inferred from a channel name or a payload
**And** that principal receives autonomous grants for ordinary consequential actions, so the unattended work the fallback used to allow still completes
**And** a gateway-driven test runs a real scheduled job that needs an ordinary consequential action and sees it complete

**Given** any ingress
**When** a `ConsentRequest` is built
**Then** its `channel` comes from ingress provenance only, never from the payload

**Given** each always-ask category (`prompt_surface`, `destructive`, `lock`, `alarm`, `authority_widening`, `owl_build`)
**When** the tripwire runs it through the run-at-once branch
**Then** it expects a prompt every time
**And** a tripwire asserts the provenance auto-grant never applies to the channel names `web` or `voice`

**Given** `tests/channels/test_unwired_channel_consent_fails_closed.py`
**When** this story lands
**Then** the test asserts the new contract (deny plus incident), and the docstring that described the old fallback is corrected

### Story 3.5: The consent address survives the gateway↔core link

As the owner,
I want a consent request to come back to exactly the chat, thread or group where the work was asked for,
So that approvals never go missing or land in the wrong conversation in split mode.

**Acceptance Criteria:**

**Given** a consent request raised in core for a turn that arrived through the gateway
**When** `ConsentRequestFrame` crosses the link
**Then** it carries `reply_target`, the frame stays typed with `extra=forbid`, and `protocol_version` is bumped (FR81, AD-18)

**Given** a turn from a Telegram group or thread
**When** consent is requested
**Then** the prompter delivers to that exact group or thread, proven through `scripts/dev_ingress.py` in split mode

**Given** a frame that arrives without a `reply_target`
**When** it is handled
**Then** the request is refused and the refusal is logged, never routed to a guessed destination

### Story 3.6: Answer from Telegram and watch it close

As the owner,
I want every Needs-you item delivered to my Telegram with Owl's exact read-back, answerable there, and updated when it resolves anywhere,
So that I can decide from wherever I am, and never act on a stale message.

**Acceptance Criteria:**

**Given** an `approval` item
**When** it is delivered to Telegram
**Then** the prompter's buttons message carries the narrator's deterministic `full` read-back (never model-written) (FR20, FR22, AD-30)
**And** each button carries only a short opaque token, well under Telegram's 64-byte `callback_data` limit, which the server maps to the item id, the item version and the digest of the request as shown

**Given** a `question`, `incident` or `alert` item
**When** it is delivered
**Then** Telegram receives the narrator's `full` text for it (FR22)

**Given** a button press
**When** it arrives
**Then** it is accepted only from the owner's allowlisted Telegram chat, its token is resolved server-side to the version and digest it showed, and it resolves through the one resolver
**And** an unknown, expired or stale token is refused, and the item is re-shown
**And** a changed request is refused and re-shown, and the first answer wins against every other surface (NFR49)

**Given** an item resolved from any surface, including by expiry
**When** the resolution commits
**Then** the Telegram message for that item is edited to show the outcome and loses its buttons (FR23)

**Given** the documentation of Telegram approvals
**When** this story lands
**Then** it states the residual risk: a hijacked Telegram account can approve irreversible actions

**Given** `device` items
**When** Epic 3 completes
**Then** they are declared as a kind, and their name-and-matching-code Telegram message is delivered by Epic 5

## Epic 4: Every change goes through one door

Every state change the owner or an owl makes, from Telegram, TUI, a slash command or an LLM tool, is one declared, authorised, audited command. Each carries a severity and reversibility and runs through the one task loop. Reversible actions can be undone until superseded or for 24 hours, and irreversible autonomous action happens only under standing authority the owner explicitly granted.

Owner decisions (2026-09-13):
- The declared-command table lives in `commands/spec/`, and the slash commands stay in `commands/` as a surface that submits.
- The severities and principal move from `control_plane` to `authz/` first.
- Existing jobs keep their irreversible actions through grandfathered standing authority.

### Story 4.1: Severities and the principal live in `authz/`

As the owner,
I want the READ, WRITE and CONSEQUENTIAL severities and the principal's `may` check to have one home in `authz/`,
So that every surface asks the same authority, and it survives the old dashboard's later deletion.

**Acceptance Criteria:**

**Given** `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES`, `ControlPrincipal` and its `may` check in `control_plane/auth.py`
**When** this story lands
**Then** they live in `authz/`, `control_plane` imports them from there, and no second definition remains (AD-7)
**And** control_plane's existing auth tests pass unchanged against the relocated code

**Given** the codebase
**When** the tripwires run
**Then** they fail any module outside `authz/` that defines a severity constant or its own principal check

**Given** the full tripwire suite
**When** it runs after the move
**Then** it passes, including the control_plane auth invariants

### Story 4.2: Every tool and slash command declares whether it changes state

As the owner,
I want every LLM tool and slash command classified as read-only or state-changing,
So that no action can change the ship without passing through the one door.

**Acceptance Criteria:**

**Given** every registered LLM tool and every slash command, including sub-commands
**When** the census is built
**Then** each one is declared either read-only or state-changing, and each state-changing entry names the command types it will submit (FR76, FR79)

**Given** a tool or slash command with no declaration
**When** the tripwires run
**Then** the census tripwire fails and names it

**Given** a state-changing entry not yet migrated to commands
**When** the census reports
**Then** it is listed as pending migration with its owning story (4.3 or 4.7–4.10), so progress is visible and nothing is forgotten

### Story 4.3: A declared command runs through one door

As the owner,
I want each state change declared once and executed only in core through one path,
So that every surface gets the same severity checks, retries never repeat a side effect, and no back door exists.

**Acceptance Criteria:**

**Given** the new `commands/spec/` sub-package
**When** a command is declared
**Then** its `CommandSpec` has a type, a typed payload model, a severity from `authz/`, a reversibility and an undo command type
**And** `commands/spec/` depends only on `authz/` and `pipeline/durable`, enforced by a tripwire
**And** its `PLACEMENT:` docstring records the owner's placement vote (AD-1, AD-7, AD-26)

**Given** the durable task store
**When** the platform migrates
**Then** an idempotent migration adds COMMAND task fields: command type, payload, `command_id`, requester kind, nonce and `utterance_id`

**Given** any surface or tool
**When** it submits a command through the one submit entry
**Then** the entry validates the payload, sets the requester kind from authenticated ingress provenance (never from the payload), and enqueues a COMMAND task keyed by `command_id`
**And** an enqueue from the gateway sends a payload-free `tasks_enqueued` wake frame, so core claims it without waiting for the tick

**Given** a COMMAND task
**When** core executes it
**Then** it runs the severity check through the principal, then a deterministic handler from core's handler registry, with no model call
**And** the subsystem mutator writes `command_id` in its own transaction, so re-execution after a lease reclaim is a no-op (AD-26)
**And** at least one worker slot stays reserved for COMMAND tasks
**And** `command.*` lifecycle events are recorded, while the mutator alone records the domain event, carrying a `CommandContext`

**Given** `CommandRegistry.dispatch` and the `??` dry-run preview
**When** a slash command that changes state is dispatched
**Then** authorisation runs through the principal before any handler or preview, and no preview returns before the severity check

**Given** the codebase
**When** the tripwires run
**Then** they fail any call to a declared mutator from outside the COMMAND handler registry, any subsystem mutator instantiated in the gateway, and any generic "run any command" entry point (FR76, FR77, FR79)

**Given** job pause and resume from the `cronjob` tool, as the first migrated commands
**When** they are driven through `scripts/dev_ingress.py`
**Then** each runs as a COMMAND task, is audited with its actor, and appears in the journal

### Story 4.4: One action-policy gate decides

As the owner,
I want one gate to decide from each command's reversibility, severity and who asked,
So that my own reversible orders run at once, owls must ask, and nothing risky happens without my explicit approval.

**Acceptance Criteria:**

**Given** the COMMAND execution path
**When** a command passes its severity check
**Then** the action-policy gate in `authz/` decides from its declared reversibility and severity plus requester kind (owner, owl or crew, `voice-unverified`, autonomous), then consent runs, then the handler (AD-27, AD-18)

**Given** the owner's own order for a reversible action at WRITE severity
**When** the gate decides
**Then** it runs at once with no read-back (FR34)

**Given** an owl or crew request
**When** the gate decides
**Then** it opens an `approval` item whose read-back is rendered deterministically from the payload by the narrator, and the answer only counts after that read-back is shown (FR35)

**Given** a step-up command (irreversible, CONSEQUENTIAL even when reversible, an owl-authority grant, or a new-device approval)
**When** the gate decides
**Then** it requires an explicit Telegram approval or a signed on-screen tap, where the signature is by the device key over a single-use, short-lived nonce bound to the command digest (type, payload, target, item id and version)
**And** a `voice-unverified` yes never satisfies it (FR36)
**And** the tap is accepted from Epic 6 onwards; until then Telegram approval is the step-up path

**Given** requester kind `voice-unverified`
**When** it orders or approves
**Then** only reversible, non-consequential commands run at once or are approved, a CONSEQUENTIAL spoken order gets a read-back and needs a step-up, and an owl-authority grant never comes through voice, proven by unit tests of the gate (FR41 rules, ahead of voice)

**Given** attendance, defined once in `authz/`
**When** a command originates from a scheduler or autonomous run
**Then** the run is never attending

**Given** a command that needs a decision
**When** consent and the gate compose
**Then** at most one Needs-you item opens for the command task, the task is parked holding no worker, and the item's waiter is the durable COMMAND task
**And** after a core restart the waiter re-materialises from durable state instead of expiring (NFR16)
**And** the gate may add friction but never removes an always-ask consent category

### Story 4.5: Undo for 24 hours

As the owner,
I want every reversible action I or Owl take to offer undo until something newer changes it or a day passes,
So that mistakes are cheap to reverse, and an undo never overwrites a newer change.

**Acceptance Criteria:**

**Given** a reversible `CommandSpec`
**When** it is declared
**Then** it names an undo command type, and a tripwire fails a reversible spec without one (FR33)

**Given** a completed reversible command
**When** the channel that ordered it renders its result
**Then** the result offers undo (for example a Telegram action button), and undo submits the undo command through the one door (FR31)

**Given** a later command that changes the same target, or 24 hours after completion
**When** undo is requested
**Then** the gate refuses it with `{code, reason, remedy}`, and the result no longer offers undo (FR88)

**Given** completed COMMAND rows
**When** the task prune runs
**Then** they are kept at least as long as the undo window and journal retention (NFR45)

### Story 4.6: Standing authority is explicit, and yours alone

As the owner,
I want irreversible actions in unattended runs allowed only where I set them up, with my existing jobs carried over,
So that autonomy never surprises me and nothing I already rely on stops working.

**Acceptance Criteria:**

**Given** no standing-authority store
**When** the platform migrates
**Then** a migration creates one `authz/` table keyed by command type and scope, writable only through `authz/` APIs (AD-27, NFR30)

**Given** an `authority.grant` or `authority.revoke` command
**When** it is decided
**Then** it needs an explicit Telegram approval or a signed tap, it never comes through voice, and no owl or tool can grant authority to itself (FR37)
**And** a tripwire fails any tool that reaches requester-kind, standing-authority or session rows

**Given** an irreversible command in a run the owner is not attending
**When** no matching standing authority exists
**Then** it becomes an `approval` Needs-you item instead of running (FR32)
**And** with matching authority it runs, and its journal record links the grant that allowed it

**Given** scheduled work
**When** a job is created
**Then** the job records which irreversible command types it was explicitly set up to take, with scope (FR33)

**Given** existing jobs
**When** this story lands
**Then** their grandfathered standing authority is not recorded here; it lands in Story 4.8, once delivery commands exist and the grandfathering can be tested for real

**Given** security evidence
**When** a standing-authority grant or revocation happens
**Then** it is also written to the hash-chained `audit_log` (NFR31)

### Story 4.7: Scheduling runs on commands

As the owner,
I want every scheduling change (create, edit, pause, resume, delete, run now) to be a declared command,
So that my jobs change only through the one door, with undo where it applies.

**Acceptance Criteria:**

**Given** the `cronjob`, `owl_schedule` and `objective_tool` tools and any slash command that changes schedules
**When** they change state
**Then** they submit declared commands with severity, reversibility and undo, and never call scheduler mutators directly (FR33, FR77, FR79)

**Given** a reversible scheduling command (for example pause, or an edit)
**When** it completes
**Then** its undo restores the prior state unless superseded or past 24 hours

**Given** the census from Story 4.2
**When** this story lands
**Then** no scheduling entry is pending migration, and the mutator tripwire covers the scheduler

**Given** a scheduling change driven through `scripts/dev_ingress.py`
**When** it runs
**Then** it lands as a COMMAND task, is visible in the journal, and a deletion from an owl's request opens an approval item

### Story 4.8: Messages and files are sent through commands

As the owner,
I want every message and file an owl sends to be a declared irreversible command,
So that nothing leaves the platform in my name without my authority.

**Acceptance Criteria:**

**Given** the `send_message` and `send_file` tools and scheduled deliveries (brief, check-in, digest, goal jobs)
**When** they deliver
**Then** they submit declared irreversible delivery commands, and never call delivery mutators directly (FR32, FR77, FR79)

**Given** an attended owner turn that asks for a message to be sent
**When** the command runs
**Then** the gate applies the step-up rule for irreversible actions requested by an owl

**Given** a scheduled delivery covered by grandfathered or explicit standing authority
**When** it runs unattended
**Then** it delivers without a new approval, and its journal record links the grant

**Given** an unattended delivery to a destination outside its standing authority's scope
**When** it is decided
**Then** it becomes an approval item and is not sent

**Given** the owner's decision to grandfather existing jobs
**When** this story's migration runs on an install with existing enabled jobs, after the scheduling (Story 4.7) and delivery command types exist
**Then** it records standing authority for each existing job's declared irreversible command types, at least delivering to the job's own target channels and addresses, with provenance `grandfathered`
**And** platform-seeded jobs receive theirs from the idempotent job seeder with provenance `seeded`
**And** both write through the same `authz/` API as `authority.grant` and record `authority.granted` journal events
**And** a gateway-driven test shows an existing morning brief and an existing goal job still deliver with no new approval
**And** the same test fails if the grandfathered rows are removed, proving the gate is actually consulted

**Given** the census
**When** this story lands
**Then** no messaging entry is pending migration

### Story 4.9: Owls, skills and tools change only through commands

As the owner,
I want creating or changing owls, skills, learned tools and owl authority to be declared commands,
So that no owl can widen its own powers, and every change is visible and approvable.

**Acceptance Criteria:**

**Given** `owl_build`, `tool_build`, the learned-tool loader and the owl and skill slash commands
**When** they change state
**Then** they submit declared commands with severity, reversibility and undo, and never call owl, skill or tool mutators directly (FR33, FR37, FR77)

**Given** a command that widens an owl's authority
**When** it is decided
**Then** it is on the step-up list, never approvable by voice, and never approvable by the owl it widens

**Given** a reversible owl change (for example rename or pause)
**When** it completes
**Then** undo restores it unless superseded or past 24 hours

**Given** the census
**When** this story lands
**Then** no owl, skill or tool entry is pending migration

### Story 4.10: Memory, config and everything else run on commands

As the owner,
I want the remaining state changes (memory, configuration, providers, preferences, notifications and plugins) moved onto commands,
So that the census finds nothing that changes the ship outside the one door.

**Acceptance Criteria:**

**Given** the remaining state-changing tools and slash commands, including `/config`, `/provider`, preferences, notifications and plugins
**When** they change state
**Then** they submit declared commands with severity, reversibility and undo, and never call subsystem mutators directly (FR76, FR77, FR79)

**Given** the census from Story 4.2
**When** this story lands
**Then** it lists zero entries pending migration, and the census tripwire fails if a new state-changing tool or slash command is added without a `CommandSpec`

**Given** a configuration change marked `sensitive`
**When** its command is recorded
**Then** no secret value reaches the journal, logs or the audit evidence

**Given** the full tripwire suite and the full test suite (run detached)
**When** they finish
**Then** both are green

## Epic 5: Open the Bridge at home and sign in

The owner sets up the Bridge through guided setup with a private per-install CA and a local name, installs it as an app on their devices and signs in with a passkey plus a recovery code. New devices are approved from a signed-in device or Telegram, and any device can be revoked. The old dashboard's setup code and brute-force brake move into `authz/identity/` first; its severities already moved in Epic 4, and `control_plane` itself keeps running unchanged until Epic 13.

This epic starts only after the Epic 1 verdicts. If B1 failed, the path the owner chose then reshapes these stories before they run.

### Story 5.1: Guided setup creates the install's own CA and local name

As the owner,
I want one guided setup command that gives my install its own certificate and local name,
So that every device at home can reach the Bridge over trusted HTTPS without a domain or a third party.

**Acceptance Criteria:**

**Given** a fresh install
**When** the owner runs `stackowl bridge setup`
**Then** it creates a CA key, signs one ECDSA P-256 server certificate with the install host name as its only SAN plus `id-kp-serverAuth`, valid for at most 825 days, stores its private key in the platform secret store and destroys the CA key (AD-14, NFR23)
**And** it shows the CA's SHA-256 fingerprint with guided trust steps for stock iPhone, Android and desktop Chrome (UX-DR33)

**Given** the host's own mDNS responder
**When** setup configures the install name under `.local`
**Then** the name is advertised through that responder, or through a bundled mDNS library (for example python-zeroconf) where the OS responder cannot publish it, such as stock Windows (AD-13, C35)
**And** when neither can publish the name, setup states this with a remedy and records it in health, never failing silently

**Given** the seeded job seeder
**When** the platform boots
**Then** exactly one certificate-expiry job exists, and it opens a Needs-you item 30 days before the server certificate expires

**Given** `stackowl bridge setup --renew`
**When** the owner renews
**Then** a new CA and server certificate are created, the new fingerprint is shown, and the guided re-trust ceremony removes the previous CA from each device (FR63, FR64)

**Given** all state written by setup
**When** paths are resolved
**Then** everything lives under `~/.stackowl/` through `StackowlHome`, secrets only in the secret store

### Story 5.2: The Bridge server runs in the gateway, over HTTPS at home

As the owner,
I want the Bridge web server to run inside the gateway, reachable only on my home network over HTTPS,
So that it stays up across core restarts and is never exposed beyond my home.

**Acceptance Criteria:**

**Given** the new `src/stackowl/bridge/` package
**When** it is created
**Then** its placement is decided by vote and argued in a `PLACEMENT:` docstring, and no other package imports it (AD-7)

**Given** the gateway starts with default settings
**When** the Bridge server starts
**Then** it runs as a `SupervisedTask` in the gateway, enabled by default with an opt-out in the `bridge` settings section (bind, port, install name) (AD-8, NFR40)
**And** the default port is a fixed value that is never 8787, the old dashboard's port until Epic 13
**And** if the port is already in use, the server refuses to start with a logged remedy, and the bridge health contributor reports it
**And** it keeps serving across a core `os.execv` restart, proven by a test (NFR12)

**Given** a fresh clone where `stackowl bridge setup` has not run
**When** the platform starts
**Then** the Bridge opens no listener and never crash-loops, the bridge health contributor reports "Bridge not set up" with the remedy `stackowl bridge setup`, and startup prints that remedy once
**And** it never falls back to plain HTTP

**Given** the listener
**When** it binds
**Then** it serves HTTPS over TCP with the setup server certificate on IPv4 only, accepts only loopback and the host's directly attached private subnets, refuses other sources with a logged remedy, and has no plain-HTTP listener (FR62, NFR25)
**And** a request by IP or any other Host redirects to the install name and never shows a passkey prompt

**Given** the unauthenticated routes
**When** the tripwires run
**Then** a tripwire pins their allowlist, and request-size and connection caps apply (AD-13, AD-38)

**Given** the Bridge server fails
**When** the health sweep runs
**Then** a bridge health contributor reports it with a remedy, and a healer restarts it where recoverable (NFR17)

### Story 5.3: The front end is built once and committed, with no drift

As the owner,
I want the Bridge front end built from pinned sources and committed as ready-to-serve assets,
So that a fresh clone needs neither Node nor a network, and the shipped app always matches its source.

**Acceptance Criteria:**

**Given** `web/bridge/`
**When** the project is created
**Then** it pins svelte 5.57.0, svelte-check 4.7.6, @sveltejs/vite-plugin-svelte 7.3.0, typescript 6.0.3 and vite 8.3.0 with a committed lockfile and a pinned Node version (Node 24 LTS, minimum 22.12.0) (AD-20, AD-21)

**Given** `vite build`
**When** it runs
**Then** minified assets are written to `src/stackowl/bridge/static/` and shipped as package data, with `build-manifest.json` holding the content hash of source and lockfile plus a `build_id`
**And** the owl mark is imported from `logo/stackowl-mark.svg`, never redrawn (AD-40)

**Given** the committed assets
**When** the Python tripwire runs
**Then** it recomputes the source-and-lockfile hash without Node and fails on a mismatch with the manifest

**Given** CI (`.github/workflows/ci-v2.yml`)
**When** the build job runs
**Then** it rebuilds with `npm ci --ignore-scripts` on the pinned Node and byte-compares `bridge/static/`, failing on any difference

**Given** the front-end assets
**When** they are built and served
**Then** no asset loads from a CDN or third-party host (NFR38)

### Story 5.4: Served under the strict security policy, installable as an app

As the owner,
I want every Bridge response locked down by a strict content policy, with the Bridge installable as an app on my phone,
So that injected text can never run as code, and the Bridge sits on my home screen like a native app.

**Acceptance Criteria:**

**Given** any Bridge response
**When** it is served
**Then** it carries the exact AD-36 `Content-Security-Policy` with `require-trusted-types-for 'script'`, `nosniff`, `no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and a `Permissions-Policy` granting the microphone to self only, pinned by a header tripwire (NFR22)
**And** exactly one named Trusted Types policy exists

**Given** `web/bridge/` source
**When** the lint tripwire runs
**Then** it fails on `{@html}`, `innerHTML` or `insertAdjacentHTML`

**Given** the service worker
**When** it is served
**Then** its script is served `Cache-Control: no-store`, it never handles API or carrier routes, and it has a versioned kill switch

**Given** the PWA manifest
**When** a device installs the Bridge
**Then** it opens standalone, with icons generated from `logo/` (FR75, UX-DR37)
**And** a client whose `build_id` differs from the server's forces a service-worker update and reload

**Given** the real committed build
**When** the automated Chromium check from spike B5 runs against it
**Then** zero CSP violations are recorded, and the policy is never relaxed to pass

### Story 5.5: One owner record, and every web handle resolves to it

As the owner,
I want the Bridge to know me as the same single owner that Telegram already knows,
So that my conversations, memory and authority are one, whichever surface I use.

**Acceptance Criteria:**

**Given** the new `src/stackowl/authz/identity/` package
**When** it is created
**Then** its placement is decided by vote, and an idempotent migration creates the owner record, passkeys, device key registry, device sessions and recovery-code hash tables (AD-17)

**Given** an install with the single allowed Telegram user
**When** the owner record is created
**Then** it formalises that user as the one owner (FR73)

**Given** an install with no Telegram owner
**When** the first passkey is enrolled
**Then** the owner record mints the owner's `identity_key`, and if a Telegram owner is configured later, that handle links to the same identity

**Given** a `web:<device>` handle
**When** `IdentityResolver` in `tenancy/` resolves it
**Then** it maps in code to the owner's tenancy principal, the owner's existing `identity_key` and the owner's `ControlPrincipal`, and no per-device alias is written to `stackowl.yaml`
**And** a tripwire asserts that a web ingress and the Telegram owner ingress resolve to the same `identity_key`, whether Telegram or the web enrolment came first
**And** a `web:` handle that does not resolve to the owner is refused, never treated as its own identity (the resolver's default of returning the handle itself never applies to `web:` or `voice:` handles)

**Given** an unreadable identity store (backend I/O error, locked keyring, permission failure)
**When** sign-in is attempted
**Then** it is refused with HTTP 503 and a remedy, the lookup is retried on the next attempt, an `incident` Needs-you item opens, and setup mode is never entered (FR74)

**Given** a damaged identity store (bytes readable but failing schema or integrity validation)
**When** the Bridge starts
**Then** the install returns to setup mode, and its setup code is shown only on the host terminal, never delivered to Telegram (AD-37)

### Story 5.6: The setup code and brute-force brake move to `authz/identity/`

As the owner,
I want the setup code and the brute-force brake to have their permanent home in `authz/identity/`,
So that neither is lost when the old dashboard is later deleted, and both protect the Bridge from day one.

**Acceptance Criteria:**

**Given** `SetupCode` (with its consumer) in `control_plane/password.py`, and `control_plane/login_guard.py`
**When** this story lands
**Then** they live in `authz/identity/`, the setup code itself stays in the platform secret store, and no second copy remains (AD-7)

**Given** the Q29 migration, the notification deliverer's setup-code hook and every other reference
**When** the move completes
**Then** they are repointed to `authz/identity/` in the same change

**Given** `control_plane`
**When** it runs after the move
**Then** its login and setup-code flow still works through the relocated code, and its existing tests pass

**Given** the tripwire suite
**When** it runs
**Then** it passes, including the control_plane auth invariants

### Story 5.7: First sign-in: setup code, then passkey

As the owner,
I want to claim my install once with a setup code and a passkey,
So that only someone with access to my host or my Telegram can ever become the owner of my Bridge.

**Acceptance Criteria:**

**Given** zero passkeys enrolled, or a damaged identity store
**When** the Bridge starts
**Then** setup mode is available; otherwise it is not, and pre-auth responses reveal setup state only inside the setup flow (AD-37, UX-DR33)

**Given** setup mode on a first install where no owner record ever existed
**When** a setup code is issued at boot or by `stackowl bridge setup-code`
**Then** it appears on the host terminal and is sent to the single allowed Telegram user, or shown on the terminal only when none or several are configured (FR67)
**And** no network request can mint a setup code (FR68)

**Given** a setup code
**When** it is presented
**Then** it is accepted only from the home network, and the brake counts setup-code, recovery-code and passkey attempts per device and globally, never per IP only
**And** past the global budget those routes refuse for a set period and open a `high` Needs-you item (NFR26)

**Given** a valid setup code on the install-name origin
**When** the owner creates a passkey
**Then** py_webauthn 3.0.0 verifies it with the RP ID set to the install name, the exact expected origin and `userVerification=required`, stores the backup-eligible and backup-state flags, and ends setup mode (FR66)
**And** the sign-in is written to the hash-chained `audit_log` (NFR31)

**Given** an already signed-in device
**When** it reopens the Bridge
**Then** no passkey prompt appears; passkey sign-in is needed only on a new device, or after revocation or expiry

### Story 5.8: Each device holds its own non-extractable key

As the owner,
I want every signed-in device to prove, on each request, that it holds its own key,
So that a stolen token is useless anywhere else.

**Acceptance Criteria:**

**Given** a successful sign-in
**When** the browser completes it
**Then** it creates a non-extractable WebCrypto ECDSA P-256 key, the gateway binds the device session to its public key, the token is kept in IndexedDB after `navigator.storage.persist()`, and the gateway stores only the token's SHA-256 (FR70, NFR21)
**And** an installed PWA and a browser tab are separate devices

**Given** any request
**When** it reaches a handler
**Then** it must carry the bearer token plus a signature over method, path, timestamp, server nonce and body hash; a missing or invalid proof gets a uniform 401, and no cookie is ever used
**And** the server issues the next single-use nonce in every response and heartbeat, so no extra round trip is needed; each nonce is accepted once, the timestamp must fall within a bounded clock-skew window, and a replayed nonce or out-of-window timestamp gets the uniform 401

**Given** token age
**When** about 30 days pass without use, or 90 days in total
**Then** the token expires and passkey sign-in is required

**Given** rotation
**When** the client rotates its token
**Then** rotation is single-flight in the client, the previous token is accepted for at most 60 s, and a retired token presented later revokes the device and opens a `high` Needs-you item

**Given** every Bridge handler
**When** the tripwires run
**Then** they enforce the invariants carried over from control_plane: fail closed without a credential; `Origin` present and checked before the token; auth inside each handler, never middleware; a uniform 401; no token or proof in logs; no credential read from a query string (NFR27)

### Story 5.9: The recovery code

As the owner,
I want a one-time recovery code I can use if I lose my devices,
So that I can always get back into my Bridge without weakening its security.

**Acceptance Criteria:**

**Given** passkey enrolment completes
**When** the recovery code is created
**Then** it carries at least 128 bits, is stored only as a slow hash, and is shown exactly once (FR72, NFR26)

**Given** the recovery code is used
**When** it is accepted
**Then** it is consumed and replaced by a new code shown once, the owner is alerted through Telegram, and the use is written to `audit_log`

**Given** a request to regenerate the recovery code
**When** it is made
**Then** until Story 6.8 lands it requires an explicit Telegram approval from the owner's allowlisted chat, and from Story 6.8 onwards a signed tap from a signed-in device

**Given** repeated wrong recovery codes
**When** they arrive
**Then** the brake refuses them per device and globally, as in Story 5.7

### Story 5.10: Approve a new device by matching its code

As the owner,
I want to approve a new device by checking that its name and code match on both screens,
So that nobody can trick me into letting their device in.

**Acceptance Criteria:**

**Given** a new device requesting access
**When** it asks
**Then** it shows its device name and a short matching code, a `device` Needs-you item opens, and only one device request may be pending at a time (AD-37, UX-DR34)

**Given** a signed-in Bridge device
**When** the owner views the request
**Then** it shows the same name and code; approval there is added by Story 6.8 as a signed tap over the single-use nonce bound to that item, and until then the device is approved in Telegram or with the recovery code (FR69)

**Given** the `device` item in Telegram
**When** it is delivered
**Then** the buttons message shows the device name and matching code, and an explicit Telegram approval from the allowlisted chat enrols the device through the one resolver, using the Story 3.6 button tokens (FR22 device, NFR49)

**Given** the recovery code
**When** it is presented on the new device instead
**Then** the device is enrolled and the code is consumed as in Story 5.9

**Given** the request expires or is refused
**When** that happens
**Then** the item resolves through the resolver, the new device is told, and nothing is enrolled

**Given** the documentation of device approval
**When** this story lands
**Then** it states the residual risk: a hijacked Telegram account plus home-network access could enrol a device and gain full Bridge control

### Story 5.11: Revoke a device

As the owner,
I want to see my signed-in devices and revoke any of them,
So that a lost or unknown device loses access immediately.

**Acceptance Criteria:**

**Given** the host CLI
**When** the owner runs `stackowl bridge sessions list` and `stackowl bridge sessions revoke <device>`
**Then** the list shows each device session with name, created and last-used times, and revoke ends the chosen session (FR71)

**Given** a revoked device
**When** it makes its next request
**Then** the request is refused, and the revocation API in `authz/identity/` closes any open carriers, voice sessions and push subscriptions it holds (they arrive in later epics and must hook into this API)

**Given** any sign-in, device request, approval, revocation, passkey change, recovery-code use or token reuse
**When** it happens
**Then** it is also written to the hash-chained `audit_log` as evidence (NFR31)

**Given** identity, device and session rows
**When** the tripwires run
**Then** they fail any agent tool that can reach them (NFR30)

### Story 5.12: WebTransport certificates rotate on their own

As the owner,
I want the short-lived certificates for the live stream generated and rotated automatically,
So that the fast stream never breaks on an expired certificate and needs nothing from me.

**Acceptance Criteria:**

**Given** the gateway starts
**When** the Bridge server is up
**Then** it generates a self-signed ECDSA P-256 WebTransport certificate valid for at most 14 days, whose key stays only in gateway memory (FR65, NFR23)

**Given** the current certificate nears expiry
**When** rotation runs
**Then** a next certificate is generated ahead of expiry, both current and next SHA-256 hashes are available to the authenticated snapshot, and the old one retires after the overlap

**Given** a gateway restart
**When** it starts again
**Then** fresh certificates are generated, and none were ever written to disk

### Story 5.13: A restore keeps you signed in

As the owner,
I want my identity, device keys and Bridge certificate included in one encrypted backup,
So that restoring a host never locks me out or breaks my devices' trust.

**Acceptance Criteria:**

**Given** the job seeder
**When** the platform boots
**Then** exactly one Bridge backup job exists, covering identity (owner, passkeys, recovery-code hash), the device key registry, and the Bridge server certificate and key (AD-39, NFR20)
**And** secret material is encrypted at rest in the backup, and WebTransport certificates are excluded

**Given** a restore
**When** it runs
**Then** it restores secrets first, then identity, keeps the install host name, and passkeys and device CA trust keep working, proven by a restore test

**Given** later epics
**When** they add Bridge state that cannot be regenerated (VAPID keys, the journal and snapshot checkpoints)
**Then** they extend this backup set rather than creating another

## Epic 6: The ship's log and Needs-you strip, live in your browser

The owner opens the Bridge and sees lightweight screens, with no 3D:
- the narrated Ship's log;
- the Crew panel;
- the owl presence mark breathing on the server heartbeat;
- the Needs-you strip on every screen.

Approvals, questions, device requests and unhealed failures are answered in place, with the signed tap where the step-up list requires one. The stream reaches the browser over WebTransport or the SSE fallback, from one snapshot and one client store, and a stale or restarting core link shows as such.

Until Epic 7, desktop shows the Crew panel and Ship's log side by side, and the Dial arrives between them. Each station epic (8–10) adds its own button and station sheet.

### Story 6.1: The live stream reaches the browser

As the owner,
I want the Bridge to receive the platform's events live over my authenticated connection,
So that what I see is always the current state of the ship, never a stale page.

**Acceptance Criteria:**

**Given** a signed-in device
**When** it opens the stream
**Then** it reads a `fetch`-streamed SSE response carrying the `Authorization` header and proof of possession on the Bridge origin, `EventSource` is never used, and a tripwire fails any route that reads a credential from a query string (AD-11, NFR14)

**Given** an authenticated stream
**When** it opens
**Then** the server sends `hello` with the heartbeat interval, the core-offline grace, `bridge_api_version` and `build_id`, and nothing streams before auth (AD-12)

**Given** an open stream
**When** each heartbeat interval passes
**Then** a `heartbeat` carries `core_link` (`up`, `restarting` or `down`), `core_last_seen_at` and `head_cursor`, and heartbeats are never journal rows

**Given** journal events after the client's cursor
**When** fan-out delivers them
**Then** they travel as `journal` envelope messages with the conventions envelope, in cursor order, with no loss or duplicates (AD-34, NFR13)

**Given** fan-out limits
**When** a device exceeds its carrier cap, or a client's bounded outbound queue overflows
**Then** the extra carrier is refused, or that client's live queue is dropped and it receives `resync`, and recording is never blocked (AD-38, NFR46)

**Given** a device revoked through the Story 5.11 API
**When** revocation happens
**Then** its stream closes within one heartbeat interval (NFR11)

### Story 6.2: One snapshot, one client store per browser

As the owner,
I want the Bridge to load the ship's current state once and keep it current from the stream,
So that every panel agrees with every other panel and with the platform.

**Acceptance Criteria:**

**Given** a signed-in device
**When** it requests the snapshot over authenticated HTTPS
**Then** `bridge/` returns, through the gateway DbPool:
- owls, channels, subsystems and memory kinds;
- jobs with their next due time;
- the ordered open Needs-you set;
- last activity per owl;
- the journal cursor this state is consistent with;
- the current and next WebTransport certificate hashes.

**And** it keeps serving these durable reads while core restarts (AD-29, NFR12)
**And** `sensitive` configuration values are redacted, pinned by a tripwire

**Given** the browser
**When** the Bridge loads in one or more tabs
**Then** exactly one client store owns the stream, cursor, heartbeat, stale state and power tier
**And** tabs share one stream through Web Locks leader election plus `BroadcastChannel`, with hand-off on close or backgrounding (AD-31)
**And** a lint tripwire fails any component that opens a stream itself

**Given** stream events after the snapshot cursor
**When** the store applies them
**Then** they are applied as upserts or invalidations keyed by target, never as increments

**Given** a `resync` message, or a cursor below the oldest retained one
**When** the store receives it
**Then** it re-runs the snapshot; a `build_id` mismatch forces a service-worker update and reload

**Given** the front end
**When** the tripwires run
**Then** they fail any hardcoded list of owls, channels, subsystems or jobs in `web/bridge/`

### Story 6.3: WebTransport as the fast path, with automatic fallback

As the owner,
I want the stream to use WebTransport when my browser supports it, and fall back by itself when it cannot,
So that I get the fastest live view without ever losing it.

**Acceptance Criteria:**

**Given** the Epic 1 B2 carrier verdict passed WebTransport interop
**When** a client connects
**Then** it opens WebTransport over HTTP/3 on the same port over UDP, using the Story 5.12 certificate hashes delivered by the Story 6.2 authenticated snapshot (AD-11, AD-14)
**And** 0-RTT early data is refused, `Origin` is checked before auth, a session whose signed auth message is late by 5 s or larger than 4 KB is closed, and QUIC address validation and connection caps apply (NFR25)

**Given** a browser without WebTransport, blocked UDP, or a handshake that still fails after one hash refresh over HTTPS
**When** the client connects
**Then** it falls back automatically to the Story 6.1 SSE carrier on the same origin, and never to plain HTTP or past a TLS error

**Given** either carrier
**When** the client resumes after a drop
**Then** both resume from the same cursor with the same envelope family, with no loss or duplicates, proven by a test that switches carriers mid-stream

**Given** the Epic 1 verdict failed WebTransport interop, or marked a client class as showing silent gaps
**When** this story is implemented
**Then** it applies the binding fail branch: SSE-only for all clients, or SSE plus POST for that client class, and the verdict is cited in the change

### Story 6.4: The Bridge shell: tokens, typefaces and the owl presence mark

As the owner,
I want a Bridge that looks like the approved design and whose owl mark shows the ship's liveness truthfully,
So that at a glance I know whether the ship is alive, stale, offline or needs me.

**Acceptance Criteria:**

**Given** the design tokens
**When** the shell renders
**Then** `--hull`, `--deck`, `--rule`, `--cream`, `--cream-dim`, `--caution`, `--caution-ink` and `--stale` are defined once in one dark theme that paints `body` and every colour explicitly, and there are no green or red status colours (UX-DR1, UX-DR2)

**Given** the three SIL OFL typefaces (Big Shoulders Display, Atkinson Hyperlegible Next, Martian Mono)
**When** the shell renders
**Then** they are vendored and served from the Bridge origin with their fallback stacks and set on the 11/13/15/18/24/36/56 px scale (UX-DR3, UX-DR4)

**Given** the header
**When** it renders
**Then** it shows the 28 px host presence mark from the `logo/` geometry, drawn as three separately lit blocks beside the host's display name (UX-DR6, UX-DR7)
**And** idle breathing follows server heartbeats, never a CSS or render loop
**And** link stale shows the mark static in `--stale` with the age of the last event
**And** ship offline is visibly distinct from link stale
**And** needs-you shows amber, solid with a slow pulse at high intensity (FR6, FR59)
**And** listening, thinking and speaking are implemented as states for the voice epics to drive

**Given** a desktop viewport
**When** the shell lays out
**Then** the Needs-you strip is on top, with the Crew panel and Ship's log beside each other below it

**Given** a phone viewport (≤ 700 px)
**When** the shell lays out
**Then** the strip is sticky under the header, and a bottom bar holds `Now` and `Log`, which switch the area between the Crew panel and the Ship's log (UX-DR24, UX-DR38)

**Given** any screen
**When** it is used with the keyboard or with reduced motion
**Then** focus is always visible, the page never scrolls horizontally, text is never clipped, `prefers-reduced-motion` stops breathing, and a visible pause control stops moving content (NFR42, NFR43)

### Story 6.5: The Ship's log and the station sheet

As the owner,
I want a plain-English log of everything the ship does, where every line opens its record,
So that I can follow and inspect the platform's work without reading raw data.

**Acceptance Criteria:**

**Given** journal events
**When** the Ship's log renders them
**Then** it shows one narrator `full` line per event, newest first, with a Data-font time; failures read as failures (for example `✕ delivery to telegram failed`), and bursts of identical events are grouped (`scheduler ran health_sweep ×6`) (UX-DR23, AD-30)
**And** every line is written from the owner's side of the screen in the platform's own vocabulary (owl names, job names, cron schedules, "dead-lettered", provider tiers), never placeholder text (UX-DR5)
**And** the log is capped at 200 lines, and its header shows `last event … ago` when stale

**Given** a log line
**When** the owner activates it with click, tap or keyboard
**Then** it is a real button that opens the station sheet on that event's record, through the registered record reader

**Given** the station sheet
**When** it opens
**Then** it is one frame: a right-side sheet 420 px wide on desktop, and an 88 vh bottom sheet on phone (UX-DR10)
**And** a gone target shows `expired`, a graph record or core-only value shows "unavailable while core restarts", and live-probe values show their `as_of` point, never a placeholder

**Given** narrated text and record content
**When** they render
**Then** they render as text nodes only

### Story 6.6: The Crew panel

As the owner,
I want one row per owl showing what it is doing right now,
So that I can see at a glance who is working and who is resting.

**Acceptance Criteria:**

**Given** the snapshot's owls
**When** the Crew panel renders
**Then** it shows one real-button row per owl, host first: module glyph, display name, doing-line, and time since last event in tabular Data font (UX-DR22)

**Given** a new event naming an owl
**When** it is applied
**Then** that owl's row moves toward the top, briefly highlights, and its doing-line updates from the event (for example `tool: process`, `task claimed`)

**Given** an owl with no recent events
**When** its row renders
**Then** it stays truthful: `resting · last active …`, or `no activity in the retained window`, never invented activity

**Given** a row
**When** the owner activates it
**Then** the station sheet opens on that owl's record (from Epic 9, on the Crew station)

**Given** an owl renamed through a command
**When** the rename event arrives
**Then** the row shows the new name without a reload

### Story 6.7: The Needs-you strip on every screen

As the owner,
I want everything that needs me in one strip at the top of every screen, and nowhere else in amber,
So that I never miss a decision, and never mistake ordinary activity for one.

**Acceptance Criteria:**

**Given** open Needs-you items
**When** any screen renders
**Then** the strip sits on the top edge (full width on desktop, sticky under the header on phone) with one amber chip per item, ordered by intensity then time opened, and the high-intensity item brighter (FR14, UX-DR8)
**And** an empty strip is a single `--rule` hairline

**Given** an item resolved on any surface, including Telegram or by expiry
**When** `needs_you.resolved` arrives
**Then** its chip leaves the strip on every device

**Given** an item whose version changed
**When** the owner is looking at it
**Then** the item is re-shown with its current version rather than answered

**Given** the front-end source
**When** the tripwires run
**Then** they fail any use of the `--caution` token outside the Needs-you strip, item cards and the presence mark's needs-you state

### Story 6.8: Answer approvals, questions and device requests in place

As the owner,
I want to answer an approval, a question or a device request right in the Bridge, with the same read-back Telegram shows,
So that I decide with full information, and my answer counts exactly once.

**Acceptance Criteria:**

**Given** an `approval` item
**When** its card opens
**Then** it shows the narrator's deterministic read-back, the same text the Telegram message carries (FR18, UX-DR9)
**And** a request that is reversible and not consequential offers a Confirm control
**And** an irreversible action, a consequential request or spoken order, or an owl-authority grant offers Confirm as a signed tap, noting it may instead be answered in Telegram

**Given** a signed tap
**When** the owner confirms
**Then** the browser signs, with its non-extractable device key, a single-use, short-lived server nonce bound to the command digest (type, payload, target, item id and version), and the gate accepts it as the step-up approval (AD-27)
**And** a replayed or expired nonce, or a digest mismatch, is refused and the item is re-shown

**Given** a `question` item
**When** its card opens
**Then** it shows the clarifying question with an answer control

**Given** a `device` item
**When** its card opens
**Then** it shows the requesting device's name and short matching code, with a signed-tap approve (UX-DR34)
**And** the same signed tap now also approves Story 5.10's device requests on a signed-in device and authorises Story 5.9's recovery-code regeneration, alongside the Telegram path

**Given** an `incident` or `alert` item
**When** its card opens
**Then** it shows the narrated cause and a link that opens its record

**Given** any answer from a card
**When** it is sent
**Then** it goes through the one resolver, and the first answer wins against Telegram
**And** a command POST returns `{command_id, task_id, trace_id}` or `{code, reason, remedy}`, and the card follows `command_result` messages (accepted, refused, awaiting decision, done, failed) (AD-34)

### Story 6.9: Failure and undo cards you can act on

As the owner,
I want cards that explain what failed and what was done, with the right actions and undo while it still applies,
So that I can fix or reverse things in one tap without digging through records.

**Acceptance Criteria:**

**Given** an unhealed-failure `incident` item
**When** its card opens
**Then** it shows what failed, what Owl tried and why no heal held, all from recorded heal and job events, with actions such as "Resume job" and "Keep paused" submitted as commands (FR17, UX-DR9)
**And** undo is offered where Owl itself paused the job, while that undo window is open

**Given** an action taken by the owner's order or by Owl within its authority
**When** its card shows
**Then** it states what was done and offers Undo until the action is superseded or 24 hours pass, after which Undo is no longer offered (UX-DR31, FR31, FR88)

**Given** a scheduled, pre-authorised irreversible run
**When** it completes
**Then** its record is a tappable card that links the standing authority that allowed it

**Given** an undo request refused by the gate
**When** the refusal returns
**Then** the card shows `{reason, remedy}` and removes the Undo control

### Story 6.10: Sounds

As the owner,
I want soft ambient cues for ordinary activity and one distinct alert when I am needed,
So that I can hear the ship without being nagged, and never miss what needs me.

**Acceptance Criteria:**

**Given** the ambient cues, the Needs-you alert and the acknowledgement sound
**When** they are produced
**Then** they are synthesized in code with Web Audio, and no audio file is bundled or downloaded (owner decision, 2026-09-13)
**And** a test for each sound proves it does not repeat within its cue window

**Given** ordinary events
**When** ambient cues play
**Then** they are very soft, never repeat or form a barrage, fall silent while the page is backgrounded, and have their own off switch; they are on by default (FR60, UX-DR29)

**Given** a `needs_you` item opening
**When** the alert plays
**Then** it is the only attention-grabbing sound, its intensity follows the item's intensity, and ambient cues never compete with it

**Given** both sound classes
**When** the owner adjusts them
**Then** each has volume control, silent mode is respected where the page can detect it, and on iOS audio starts only after a user gesture

**Given** a burst of events
**When** cues would overlap
**Then** they are coalesced, so no more than one cue plays per short interval

### Story 6.11: A stale or restarting ship never looks alive

As the owner,
I want the Bridge to show honestly when the stream is stale or core is offline, and to recover by itself,
So that I never trust a frozen screen as the live ship.

**Acceptance Criteria:**

**Given** a live stream on either carrier
**When** the stream is killed
**Then** link stale shows within one heartbeat timeout: breathing stops, panels dim to `--stale`, and `last event … ago` ticks up (NFR11, UX-DR25)

**Given** a core `os.execv` restart
**When** `core_link` is not `up` past the announced grace
**Then** "ship offline" shows, visibly distinct from link stale, while the snapshot, record readers and command enqueue keep working (NFR12, FR82)

**Given** a backgrounded phone
**When** the page becomes visible again
**Then** stale is evaluated from visibility and carrier events, not only throttled timers, and the store resumes from its cursor

**Given** the link returns
**When** the store resumes
**Then** stale clears, and the replayed events show no loss or duplicates

**Given** each scenario above
**When** the end-to-end test runs
**Then** it drives real stream kills and a real core restart through the running platform, not mocks, on both carriers

## Epic 7: The Helm Dial Viewscreen

The live ship on the Helm Dial: owls and what they are doing, missions in flight, jobs approaching their due time, memory, engineering and comms. Every mark is caused by a real event and opens its record, a quiet or failing ship looks quiet or failing, and a low-power 2D mode shows the same truth with less spectacle.

Spikes B4 and B3 run first on a throwaway prototype replaying a real recorded day from the journal. Their verdicts gate the visual grammar and the device tiers. Station names on the Dial rim are added by each station epic (8–10), so FR1 is complete when Epic 10 lands.

### Story 7.1: Every event type gets one truthful visual (spike B4 kit)

As the owner,
I want every recorded event type mapped to one visual and replayed over a real day of my platform,
So that the Dial's motion is proven truthful before it is built.

**Acceptance Criteria:**

**Given** a throwaway kit under `spikes/bridge-dial/`, marked throwaway, with its dependencies declared inline
**When** it exports one real day of journal events through a read-only connection (`mode=ro`), keeping metadata only
**Then** it replays that day on a prototype Helm Dial built from the approved v2 motion grammar (UX-DR20)

**Given** the event registry
**When** the kit builds its mapping
**Then** every registered event type is mapped either to exactly one visual or to "log-only", and an unmapped type fails the kit's check

**Given** the replay
**When** it runs
**Then** every drawn mover traces to a journal event or snapshot record, which the kit verifies by recording mover → cursor links
**And** no mover appears without a referent, and stopped traffic is not animated

**Given** the replay results
**When** the kit writes them
**Then** `spikes/bridge-dial/results/B4-mapping.json` holds the type → visual table, the log-only types and the referent check outcome

**Given** Chromium on the build host
**When** the automated check runs
**Then** it proves the mapping is complete and every mover has a referent

### Story 7.2: The Dial holds its frame budget on older phones (spike B3 kit)

As the owner,
I want the Dial's rendering cost measured for 30 minutes on a mid-range Android and an older iPhone,
So that each device gets the richest view it can hold without throttling.

**Acceptance Criteria:**

**Given** the B4 prototype scene in `three/webgpu` (`WebGPURenderer` with automatic WebGL 2 fallback)
**When** the kit replays 30 minutes of recorded events at recorded rates, including bursts
**Then** it measures frame work time against the observed `requestAnimationFrame` cadence, sustained dropped frames, and throttling onset (AD-20)

**Given** a drain budget set before the run
**When** a burst arrives
**Then** the kit records whether the queued travellers drain within that budget

**Given** sustained over-budget frames, or `prefers-reduced-motion`
**When** either occurs
**Then** the prototype enters its low-power mode, and the kit records that the trigger fired (never from battery APIs)

**Given** each device run
**When** it ends
**Then** P50 and P95 frame work time, dropped-frame counts, the render backend used, and the low-power trigger are written to `spikes/bridge-dial/results/B3-<device-class>.json`

**Given** Chromium on the build host
**When** the automated check runs
**Then** it covers measurement, the WebGL 2 fallback and the low-power trigger

### Story 7.3: Dial verdicts decide the grammar and the tiers

As the owner,
I want the B3 and B4 results turned into verdicts that fix the visual grammar and each device's default tier,
So that the Dial is built on what my devices and my real data proved.

**Acceptance Criteria:**

**Given** the B4 and B3 result files
**When** the owner runs the kit's verdict command
**Then** it writes `docs/agentic-os-dashboard/spikes/epic-7-dial-verdicts.md` with:
- the final event type → visual mapping;
- the log-only types;
- the B3 result per device class and the tier thresholds;
- a verdict per spike.

**And** a device class with no result is "not run", never a pass (gates AD-3, AD-20)

**Given** a failing result
**When** the verdict is written
**Then** it names the binding fail branch: a device class that fails B3 defaults to low-power 2D; an event type without a truthful visual appears only in the Ship's log and 2D views, never as an invented mover

**Given** the kits from Stories 7.1 and 7.2
**When** this story completes its agent work
**Then** they are preserved on a local `spike/bridge-epic-7` branch and deleted from main in the same change

**Given** the agent work is committed
**When** the story finalises
**Then** its spec is set to `status: awaiting-operator`, and `operator_actions` lists:
1. Clone `spike/bridge-epic-7` into a fresh directory on the platform's own host on the home network.
2. Run B3 for 30 minutes each on a mid-range Android and an older iPhone, including iOS Low Power Mode.
3. Watch the B4 replay, and confirm that quiet or failing reads as such and that idle and event motion are told apart at a glance.
4. Run the verdict command and commit the verdict document.
5. Run `bmad-loop confirm` for this story.

### Story 7.4: The Dial frame: rings, labels and the host at the centre

As the owner,
I want the Helm Dial at the centre of my Bridge, with every ring labelled and the host owl at its heart,
So that I can read the ship's layers without a legend.

**Acceptance Criteria:**

**Given** a desktop viewport
**When** the Bridge renders
**Then** it lays out three columns: the Crew panel on the left (280 px), the Dial in the centre (square, as large as fits) and the Ship's log on the right (340 px), with the Needs-you strip on top (UX-DR12)
**And** only the Dial uses `three/webgpu` with automatic WebGL 2 fallback, and every panel stays Svelte DOM (FR61)

**Given** the Dial
**When** it renders
**Then** each ring (`SCHEDULE`, `COMMS`, `CREW`, `MEMORY`, `ENGINEERING`) carries its own uppercase Display label on its own arc, placed by computed angle with collision nudging so no labels ever overlap (UX-DR13)
**And** the host sits at the centre as the full logo mark, animated by the Story 6.4 presence states, with its display name and doing-line beneath (UX-DR19)
**And** colours come from the same tokens as the DOM (UX-DR1)

**Given** the rendering loop
**When** nothing changes
**Then** it renders on demand, caps the ambient tick at 15–30 fps, uses full rate only during event transitions, and draws nothing while the page is hidden (NFR10)
**And** idle breathing follows the server heartbeat

**Given** every canvas entity
**When** it is drawn
**Then** a visually hidden DOM/ARIA twin list holds a real button for it, which is also the keyboard path, and no text lives only in the canvas (UX-DR28, NFR41)

### Story 7.5: The SCHEDULE bezel shows what is coming

As the owner,
I want the Dial's outer bezel to show a 24-hour clock with my live jobs at their due times,
So that I can see what the ship will do next, not just what it did.

**Acceptance Criteria:**

**Given** the snapshot's jobs with next due times
**When** the bezel renders
**Then** it shows a 24-hour clock in local time with Data-font hour numerals, a live "now" hand, and a tick at each due time in the next 24 hours for every live recurring job; completed one-shots are not drawn (UX-DR14, FR3)

**Given** the next three due jobs
**When** the bezel renders
**Then** they are written as short labels beside the hand (for example `telegram_canary · 15:00`), without overlapping the numerals

**Given** a `job.*` run event
**When** it arrives
**Then** the job's tick flares, and a thin light travels inward along a radius to the actor that handled it in ENGINEERING; a failed run's light is dashed and stops short

**Given** a job created, paused, resumed or deleted by a command
**When** its event arrives
**Then** the tick appears, dims or disappears without a reload, applied as an upsert keyed by the job

**Given** a bezel tick
**When** the owner activates it
**Then** its record opens in the station sheet

### Story 7.6: The COMMS ports and the CREW ring

As the owner,
I want to see messages arrive through their channels and each owl working on the crew ring,
So that I can watch conversations and crew activity as they actually happen.

**Acceptance Criteria:**

**Given** the snapshot's channels
**When** the rim renders
**Then** each channel present has one labelled docking port at a fixed rim angle (UX-DR15)

**Given** a turn arriving and its delivery
**When** the events arrive
**Then** a packet travels from the port to the host, and the delivery travels from the host or owl back to the port
**And** a failed delivery bounces back dashed and leaves a small hollow mark with a count on the port

**Given** the snapshot's owls
**When** the crew ring renders
**Then** each owl is an evenly spaced module with a three-bar glyph derived from the mark (never the full mark), its display name and a one-line doing text (UX-DR16)

**Given** owl events
**When** they arrive
**Then** a module's state is shown by form, all in cream:
- resting is dim hollow bars;
- thinking lights the bars in sequence on a model call;
- using a tool shows the top bar solid with a spoke;
- waiting for consent shows a lock glyph;
- needs-you shows an amber ring, only when a Needs-you item names this owl.

**And** non-owl actors (`scheduler`, `loop`) are never drawn as crew (FR5)

**Given** a delegation or a task claim
**When** the event arrives
**Then** a curved arc links the two modules (or host and module), or a square token travels along the crew ring from `loop` to the owl that claimed it

### Story 7.7: The MEMORY arcs and the ENGINEERING band

As the owner,
I want to see memories being written and the ship's engineering health on the Dial,
So that learning, self-healing and trouble are visible as they happen.

**Acceptance Criteria:**

**Given** the snapshot's memory kinds
**When** the inner ring renders
**Then** it is split into labelled arcs by the real kinds reported, each with a live count, falling back to one arc when the kinds are unknown (UX-DR17)

**Given** a memory write event
**When** it arrives
**Then** a particle flows from the writing module into its arc, the arc segment brightens for 1.5 s, and the count ticks

**Given** the actors and targets that actually appear in events
**When** the lower 120° ENGINEERING band renders
**Then** it shows labelled segments for those subsystems, capped at about 8, with the rest grouped as `other`, plus a labelled `DEAD LETTER` tray with a count (UX-DR18)

**Given** engineering events
**When** they arrive
**Then** they render as follows:
- a heal pulses its segment cream once and leaves a `healed` tick for 30 s;
- a health change is solid for ok, hollow or dashed for degraded;
- an incident draws a hollow outline, amber only when a Needs-you item exists for it;
- a budget alert ticks the `fuel` gauge;
- a dead-lettered task token drops into the tray.

**Given** any arc, segment or the tray
**When** the owner activates it or its DOM twin
**Then** its record opens in the station sheet

### Story 7.8: Motion that is always true

As the owner,
I want every motion on the Dial to follow one learnable grammar tied to a real event, even in a burst,
So that I can trust what moves and never mistake noise for activity.

**Acceptance Criteria:**

**Given** the Story 7.3 verdict mapping
**When** events arrive
**Then** each type renders with its one mapped motion (turn, delivery, model call, tool call, delegation, task, job run, memory write, engineering behaviours, consent lock), each at most 900 ms of travel followed by a 2 s fade trail (UX-DR20)
**And** each log-only type renders only in the Ship's log and the 2D views

**Given** a burst
**When** concurrent travellers would exceed the cap (about 24, or the value set by B2/B4)
**Then** the overflow aggregates into a `+N` pulse on the target, and the view declares that it samples (UX-DR21)

**Given** any mover
**When** the owner taps or clicks it, or activates its DOM twin
**Then** its own record opens in the station sheet, and a gone target opens as `expired` (FR4)

**Given** the motion rules
**When** they are tested
**Then** automated checks prove:
- idle motion is low-amplitude and distinct from event motion;
- state transitions take at most 600 ms;
- intensity scales with severity, never with activity volume;
- nothing flashes more than three times per second;
- no mover exists without a journal event or snapshot record (FR5, UX-DR27, NFR42)

### Story 7.9: Rim station labels and the phone posture

As the owner,
I want station names on the Dial's rim on desktop, and a compact Dial on my phone,
So that the Bridge is navigable in both postures without clutter.

**Acceptance Criteria:**

**Given** the Dial's outer rim on desktop
**When** station labels are registered
**Then** each renders as a Display-font real button placed by computed angle so it never overlaps bezel numerals, next-due labels or ring labels, and opens the station sheet on its station (UX-DR38)
**And** the slot mechanism accepts labels from the station epics (Comms in Epic 8, Crew and Missions in Epic 9, Engineering, Archives and Security in Epic 10), so no label is drawn for a station that does not exist yet (FR1)

**Given** a phone viewport (≤ 700 px)
**When** the Bridge renders
**Then** the Dial sits on top at full width, with ring labels shortened and next-due labels hidden, above the Story 6.4 `Now` and `Log` area and bottom bar, with the Needs-you strip sticky under the header (UX-DR24, FR2)

**Given** both postures
**When** the viewport resizes across 700 px
**Then** the layout switches without a reload, and nothing overlaps or scrolls horizontally (NFR43)

### Story 7.10: Low-power 2D mode

As the owner,
I want a 2D view that shows the same events with less motion whenever my device cannot keep up or I prefer it,
So that the Bridge stays truthful and usable on every phone.

**Acceptance Criteria:**

**Given** measured frame work time against the observed `requestAnimationFrame` cadence with sustained dropped frames, or `prefers-reduced-motion`, or the owner's visible motion control
**When** any of them applies
**Then** the Viewscreen enters low-power 2D mode, and it is never triggered from battery APIs (UX-DR26)

**Given** low-power 2D mode
**When** events arrive
**Then** there is no continuous motion and no travel, targets update as static state changes, the Ship's log keeps streaming, and every mark still opens its record (FR61)

**Given** a device class that failed B3 in the Story 7.3 verdict
**When** the Bridge loads on it
**Then** low-power 2D mode is its default, and the owner can still switch to the full Dial

**Given** tier thresholds from the B3 verdict
**When** the render tier is chosen
**Then** those thresholds are used, and the chosen tier and its reason are visible in the motion control

## Epic 8: Talk to Owl in Comms

The owner converses with Owl in the browser, in the same conversation, memory and task loop as Telegram, with action buttons. The owner can browse their Telegram, Slack and TUI threads, see failed deliveries and the undelivered outbox, and Owl is the ship's renamable host. An existing host name, such as this install's "Friday", is never overwritten.

### Story 8.1: The `web` conversation channel

As the owner,
I want to talk to Owl from the Bridge in the very same conversation I have on Telegram,
So that I never have two assistants with two memories.

**Acceptance Criteria:**

**Given** the gateway starts with the Bridge enabled
**When** channels register
**Then** a `ChannelAdapter` named `web` in `bridge/` registers in the live `ChannelRegistry`, and its `PLACEMENT:` docstring argues its home (AD-7, FR85)

**Given** a message from a signed-in device
**When** it enters through the `web` adapter
**Then** its `web:<device>` handle resolves to the owner's `identity_key`, so the turn joins the same conversation, memory and task loop as the owner's Telegram turns
**And** the requester kind is set from authenticated ingress provenance (AD-17)

**Given** the `web` channel
**When** consent is needed during a web turn
**Then** its prompter is registered from the live channel registry, the approval opens a Needs-you item answered on a Bridge card (Story 6.8), and the provenance auto-grant never applies to `web` (AD-18)

**Given** a web turn driven end to end, with only the AI provider mocked
**When** it completes
**Then** the reply reaches the Bridge, a later Telegram turn can recall it, and `channel.*` events are in the journal

### Story 8.2: Owl's replies stream into Comms, with action buttons

As the owner,
I want Owl's reply to appear as it is written, with any action buttons that come with it,
So that talking in the Bridge feels live, and I can act on a reply in one tap.

**Acceptance Criteria:**

**Given** a web turn producing response chunks
**When** they stream
**Then** they travel as `conversation` envelope messages on the device's carrier and render incrementally in Comms (AD-34)
**And** `conversation` messages are not resumable, so after a reconnect the transcript reopens from its record

**Given** a `ResponseChunk` with `actions`
**When** it renders
**Then** each action is a real button that submits its command or answer through the one door or the one resolver, and shows the `command_result`

**Given** a `clarify_ask` in a web turn
**When** it renders
**Then** it appears as the Story 6.8 question card inside Comms, answered through the resolver

**Given** a file or attachment sent to the web channel
**When** the owner opens it
**Then** it is served with `Content-Security-Policy: sandbox`, `Content-Disposition: attachment` and `nosniff`, never as an active same-origin document (AD-36)

**Given** reply text
**When** it renders
**Then** it renders as text nodes only, with no HTML injection

### Story 8.3: The Comms station: talk and browse your threads

As the owner,
I want a Comms station where I talk to Owl and read my other conversations,
So that all my channels are in one place on the Bridge.

**Acceptance Criteria:**

**Given** the Comms station
**When** it registers
**Then** it adds its label to the Dial rim slots (Story 7.9) and its button to the phone bottom bar, and on phone Comms is one swipe from the Viewscreen (UX-DR24, UX-DR38)

**Given** the owner's Telegram, Slack and TUI threads
**When** the owner browses them in Comms
**Then** the thread list and transcripts come from registered, authority-checked record readers over the existing sessions and messages tables, served through the gateway DbPool (FR7, AD-4)
**And** no message content is copied into the journal, and transcripts render as text nodes

**Given** core is restarting
**When** the owner browses transcripts
**Then** they keep loading, because they are durable reads (NFR12)

**Given** a thread
**When** the owner opens it on a phone
**Then** the transcript opens in the 88 vh bottom sheet, and keyboard and screen-reader navigation work

### Story 8.4: Delivery failures and the undelivered outbox

As the owner,
I want to see every message that failed to reach me and choose what to do with it,
So that nothing Owl tried to tell me is silently lost.

**Acceptance Criteria:**

**Given** failed delivery attempts and the undelivered outbox
**When** the owner opens that view in Comms
**Then** each entry shows destination, channel, when it failed, attempts and error code, read through registered readers (FR7)

**Given** an entry
**When** the owner retries or discards it
**Then** a declared command from the Epic 4 messaging commands runs through the one door, retry is gated as an irreversible delivery, and the entry updates from the journal event

**Given** a delivery that later succeeds
**When** its event arrives
**Then** the entry leaves the outbox view on every device

### Story 8.5: Owl is the host, renamable; the crew speak when addressed

As the owner,
I want the host owl to carry the name I choose, and the other owls to speak only when I address them,
So that the Bridge feels like one crew with one host, not a chorus.

**Acceptance Criteria:**

**Given** a new install
**When** the host owl (the Secretary) is created
**Then** its display name defaults to "Owl" (FR30)

**Given** an install whose host already has a display name (for example "Friday")
**When** this story lands
**Then** that name is kept and never overwritten

**Given** the owl rename command
**When** the owner renames the host or a crew owl
**Then** it runs through the one door with undo, and the header, Crew panel, Ship's log and Comms show the new name live through the narrator's `NameResolver` ports

**Given** a Comms message that addresses a crew owl by name
**When** it is routed
**Then** the existing name-based routing sends it to that owl, and the reply shows which owl is speaking

**Given** a message that addresses no owl
**When** it is routed
**Then** the host answers, and crew owls stay silent

## Epic 9: Crew and Missions: see and steer the work

The owner sees each owl's activity, authority, skills and DNA, and every task, schedule, retry and dead letter. They steer the work through the first control actions (pause, resume, retry, cancel, run now, grant, steer, take over), each shown with its result and undo where reversible.

The platform has no owl pause, skill toggle or task taken-over state today, so this epic adds each mechanism by migration, as a declared command.

### Story 9.1: The Crew station: each owl's activity, authority, skills and DNA

As the owner,
I want a Crew station showing what each owl does, what it may do, its skills and its DNA,
So that I understand my crew before I change anything about it.

**Acceptance Criteria:**

**Given** the Crew station
**When** it registers
**Then** it adds its label to the Dial rim slots and its button to the phone bottom bar, and Crew panel rows and crew modules now open it on that owl (UX-DR38, FR8)

**Given** an owl
**When** its Crew record opens
**Then** it shows:
- recent activity from the journal;
- authority as the owl's bounds intersected with its creation ceiling, computed in `authz/` and never by the browser;
- skills from skill ownership;
- DNA from DNA storage.

All of it is read through registered, authority-checked readers.

**Given** core is restarting
**When** the station shows core-only values
**Then** they show as unavailable, while durable values keep loading

**Given** the recorded interactions between agents
**When** the owner opens an owl's Crew record or the crew interaction view
**Then** the edges between owls (delegations and handoffs) are shown as a set from their registered reader, replacing the old dashboard's interactions view

**Given** an owl whose authority, skills or DNA change through a command
**When** the event arrives
**Then** the Crew record updates without a reload

### Story 9.2: Pause and resume an owl

As the owner,
I want to pause an owl so it takes no new work, and resume it later,
So that I can stop a misbehaving owl without deleting it.

**Acceptance Criteria:**

**Given** no owl paused state exists today
**When** the platform migrates
**Then** an idempotent migration adds a paused state for owls, and the owl's current lifecycle is preserved

**Given** `owl.pause` and `owl.resume` declared as reversible WRITE commands with undo
**When** the owner pauses an owl from Crew
**Then** the command runs through the one door, the owl stops receiving new turns and scheduled work, and routing and the scheduler skip it with a narrated reason recorded in the journal (FR8, FR13)
**And** work the owl already holds follows its normal lifecycle and is not killed silently

**Given** a message addressed to a paused owl
**When** it is routed
**Then** the owner is told the owl is paused, and the host answers instead

**Given** the host owl (the Secretary)
**When** a pause is requested for it
**Then** it is refused with `{code, reason, remedy}`, because the host is mandatory

**Given** a paused owl
**When** the owner resumes it or uses undo within the window
**Then** it receives work again

### Story 9.3: Grant authority and manage an owl's skills from Crew

As the owner,
I want to widen an owl's authority or switch its skills on and off from Crew, with the safety checks that deserves,
So that my crew's powers change only by my explicit decision.

**Acceptance Criteria:**

**Given** a grant from Crew
**When** the owner submits it
**Then** it is an always-ask `authority_widening` command that shows the narrator's read-back and requires a signed tap or an explicit Telegram approval, never voice, and never approvable by the owl it widens (FR8, FR36, FR37)

**Given** no skill enable or disable exists today
**When** this story lands
**Then** `skill.enable` and `skill.disable` are declared reversible commands per owl, with undo, and a disabled skill is no longer offered to that owl's turns (FR13)

**Given** the rename command from Story 8.5
**When** the owner renames an owl from Crew
**Then** it runs through the same command, with undo

**Given** each of these commands
**When** it completes
**Then** its result shows on the Story 6.9 card, and the Crew record updates from the journal

### Story 9.4: The Missions station: tasks, history, schedules, retries and dead letters

As the owner,
I want one station showing all work, past and pending,
So that I can see what is running, what finished, what keeps failing and what gave up.

**Acceptance Criteria:**

**Given** the Missions station
**When** it registers
**Then** it adds its label to the Dial rim slots and its button to the phone bottom bar (UX-DR38)

**Given** durable tasks and jobs
**When** Missions opens
**Then** it shows:
- tasks in flight;
- finished-task history within journal retention;
- schedules with their next due time;
- retries with attempt count and last error code;
- dead letters.

All of it comes through registered readers (FR9)

**Given** a job whose state is inconsistent (for example a completed one-shot still enabled with a past next run)
**When** it is listed
**Then** it is shown exactly as stored, never hidden or corrected by the view

**Given** a task or job
**When** the owner opens it
**Then** its record shows its journal history, and a gone target shows `expired`

### Story 9.5: Job and task controls from Missions

As the owner,
I want to pause, resume, run, retry and cancel work from Missions,
So that I can steer the ship's workload in one place.

**Acceptance Criteria:**

**Given** a job
**When** the owner pauses, resumes or runs it now from Missions
**Then** the Story 4.7 scheduling commands run through the one door, with their result and undo card where reversible (FR13)

**Given** no task retry or cancel command exists yet
**When** this story lands
**Then** `task.retry` and `task.cancel` are declared commands: retry resets the attempt under the loop's normal backoff, and cancel is irreversible and gated accordingly

**Given** a `??` dry-run of any Missions control
**When** it is requested
**Then** it is evaluated only after the severity check, and runs nothing

**Given** `/bye`, `/provider`, `/connect`, owl grant, `/memory forget` and `/cost privacy`
**When** they are reached from the Bridge
**Then** they stay consent-gated

**Given** a control refused by the gate
**When** the refusal returns
**Then** Missions shows `{reason, remedy}`, and nothing changes

### Story 9.6: Steer a mission

As the owner,
I want to give a running or queued mission new guidance,
So that I can correct its direction without stopping it.

**Acceptance Criteria:**

**Given** `mission.steer` declared as a WRITE command carrying the owner's guidance
**When** the owner steers a mission that has a live turn
**Then** core sends the guidance to the gateway in a new typed steer frame (in `ipc/frames.py`, `extra=forbid`, with a `protocol_version` bump), the gateway passes it to `TurnRegistry.try_steer`, and the turn continues with it (FR9, AD-33)
**And** core never instantiates the `TurnRegistry`, and a steer frame for a turn that has already ended is refused, with `{code, reason, remedy}` returned to the command

**Given** a queued mission with no live turn
**When** the owner steers it
**Then** the guidance is added to the task's constraints, and the next attempt uses it

**Given** the owner's own steer
**When** the gate decides
**Then** it runs at once as the owner's order, is recorded in the journal without its text content, and shows on the mission card

**Given** a mission that finished before the steer arrived
**When** the command executes
**Then** it is refused with `{code, reason, remedy}`

### Story 9.7: Take over a mission

As the owner,
I want to take a mission out of an owl's hands, edit its plan, and then resume or cancel it,
So that I can fix a mission myself and be certain it never continues without me.

**Acceptance Criteria:**

**Given** no taken-over state exists for tasks today
**When** the platform migrates
**Then** an idempotent migration adds the taken-over state, and a taken-over task is never claimed by the loop (FR89)

**Given** a mission in Missions, or an owl's mission in Crew
**When** the owner takes it over
**Then** a `mission.take_over` command pauses it: no worker holds it, and a running turn stops before its next model call or tool call
**And** a tool call already in flight finishes and its result is recorded, never killed mid-write, and the pause is recorded in the journal

**Given** a taken-over mission
**When** its plan opens in the station sheet
**Then** an objective shows its ordered subgoals (goal text, order, dependencies), and a single task shows its goal and achievement condition, all editable (UX-DR39)

**Given** an edited plan
**When** the owner resumes
**Then** `mission.resume` writes the edited plan and returns the mission to the loop

**Given** the owner cancels instead
**When** they do
**Then** `mission.cancel` ends it as an irreversible command, gated accordingly

**Given** a taken-over mission
**When** time passes, core restarts or the owner leaves
**Then** it stays paused and taken over, and never continues silently

## Epic 10: Engineering, Archives and Security

The owner sees:
- health, providers, cost and self-healing incidents over time;
- core-only live values;
- what the ship knows about them and why it decided;
- flight-recorder replay of any past window;
- active authority, approval history, devices and the audit trail.

The Engineering, Archives and Security rim labels land here, completing FR1.

### Story 10.1: Core-only live values through typed query frames

As the owner,
I want the Bridge to ask core for values that live only in its memory,
So that I can see live grants, breakers, context windows and worker occupancy honestly, with their age.

**Acceptance Criteria:**

**Given** the existing gateway↔core link
**When** the gateway needs active session consent grants, live health, provider breakers, context windows or worker occupancy
**Then** it sends a typed request frame and core answers with a typed response carrying `as_of_cursor`, over the same link with no second socket or connector (FR83, AD-10)
**And** the new frames live in `ipc/frames.py` with `extra=forbid`, and `protocol_version` is bumped

**Given** core is restarting or the link is down
**When** a query is made
**Then** the Bridge shows those values as "unavailable while core restarts", never stale numbers presented as live

**Given** a query response
**When** the Bridge renders it
**Then** each value is labelled as a live probe with its `as_of` point, distinct from cursor-consistent snapshot values

**Given** graph record readers
**When** they are needed
**Then** they use the same query-frame path (AD-4)

### Story 10.2: The Engineering station

As the owner,
I want one station for providers, health over time, cost and processes,
So that I can see how the ship's machinery is doing now and how it has been doing.

**Acceptance Criteria:**

**Given** the Engineering station
**When** it registers
**Then** it adds its label to the Dial rim slots and its button to the phone bottom bar (UX-DR38)

**Given** `health.changed` events within retention
**When** the health view opens
**Then** it shows each subsystem's status on a time axis, not only the current snapshot (FR10, FR84)

**Given** providers and models
**When** they are shown
**Then** configured providers and models come from durable readers, and breakers and context windows come from the Story 10.1 query frames with their `as_of`

**Given** cost records
**When** the fuel view opens
**Then** it shows spend over time against any budget, and budget alerts link their Needs-you items

**Given** processes and workers
**When** they are shown
**Then** worker occupancy comes from the query frame, and managed processes from their registered readers

**Given** the platform's configuration
**When** the config view opens in Engineering
**Then** every configured setting is listed with `sensitive` values masked, read through a registered reader, replacing the old dashboard's config view

**Given** Engineering
**When** any value is unavailable
**Then** it says so truthfully, never with a placeholder

### Story 10.3: Self-healing incidents you can read

As the owner,
I want every self-healing incident recorded with what failed, what Owl tried and how it ended,
So that I can trust the self-healing and understand what still needs me.

**Acceptance Criteria:**

**Given** incidents today are written only as `audit_log` rows by `incident_escalation`
**When** the platform migrates
**Then** an idempotent migration creates an incidents table owned by the health subsystem, and incident escalation writes to it through its mutator, in the same transaction as its journal events (AD-4, AD-24)
**And** `audit_log` keeps only its evidence role

**Given** an incident
**When** its record opens in Engineering
**Then** it shows what failed, each heal attempt Owl made, and why no heal held, or that a heal succeeded, from recorded heal and incident events (FR10)

**Given** `heal.healed`
**When** it resolves an incident
**Then** the incident shows as healed, with no amber, and never enters the Needs-you strip

**Given** `heal.exhausted`
**When** it is recorded
**Then** the incident links its `high` Needs-you item, and its record is openable from the Story 6.9 card

**Given** incident rows referenced by events
**When** the prune runs
**Then** they are kept at least as long as journal retention (NFR45)

### Story 10.4: The Archives station: what the ship knows and why it decided

As the owner,
I want to read what the platform knows about me and why it made each decision,
So that nothing about me or my assistant's reasoning is hidden.

**Acceptance Criteria:**

**Given** the Archives station
**When** it registers
**Then** it adds its label to the Dial rim slots and its button to the phone bottom bar

**Given** `USER.md` and the curated owl files under `StackowlHome`
**When** the owner opens them in Archives
**Then** they are read through the registered md reader, read-only and authority-checked, and rendered as text nodes (FR11, AD-4)

**Given** the decision ledger
**When** the owner opens a turn's decisions
**Then** the per-turn history is read through the `turn_decisions` reader and linked from that turn's journal events

**Given** what the platform has learned (lessons, reflections and committed facts)
**When** the owner opens it in Archives
**Then** each kind is listed with its items through registered readers, rendered as text nodes, replacing the old dashboard's memory view

**Given** a curated file changed by a memory write
**When** its `memory.*` event arrives
**Then** the open view refreshes to the file's current content

### Story 10.5: Snapshot checkpoints

As the owner,
I want the platform to keep periodic checkpoints of the ship's state,
So that the flight recorder can replay any past window from real state, never invented state.

**Acceptance Criteria:**

**Given** no checkpoint store exists
**When** the platform migrates
**Then** an idempotent migration creates a checkpoints table owned by `journal/`, holding a metadata-only snapshot (the AD-29 shape) with its cursor (AD-35)

**Given** the job seeder
**When** the platform boots
**Then** exactly one checkpoint job exists, and it writes a checkpoint at the interval set in settings (provisional until B2)

**Given** the Story 2.11 prune job
**When** it prunes the journal
**Then** it prunes checkpoints with it, and always keeps the newest checkpoint at or before the oldest retained cursor (NFR44)

**Given** the Story 5.13 backup set
**When** this story lands
**Then** the backup covers the journal and the checkpoints, restored after secrets and identity (AD-39)

### Story 10.6: The flight recorder

As the owner,
I want to replay a past window of the ship exactly as it happened, fast or slow,
So that I can see what happened last night without reading logs.

**Acceptance Criteria:**

**Given** Archives
**When** the owner chooses a past window
**Then** replay starts from the newest checkpoint at or before the window and applies the retained events after it, through the same Dial and Ship's log rendering in replay mode (FR27, AD-35)

**Given** the replay controls
**When** the owner uses them
**Then** a time-lapse compresses the window to about 30 seconds, a speed control changes pace, and "back to live" returns to the live stream (UX-DR35)

**Given** replay mode
**When** it renders
**Then** recorded past is drawn in `--cream-dim`, and nothing in replay can be mistaken for live activity

**Given** a requested time before the oldest checkpoint
**When** replay reaches it
**Then** it shows "unknown before retention" and never invents state

**Given** a replayed mark
**When** the owner opens it
**Then** its record opens, or shows `expired` if the target is gone

### Story 10.7: The Security station

As the owner,
I want one station showing who and what has authority, what was approved, which devices are signed in, and the audit trail,
So that I can verify my install's security at a glance and fix it in one place.

**Acceptance Criteria:**

**Given** the Security station
**When** it registers
**Then** it adds its label to the Dial rim slots and its button to the phone bottom bar, completing the six rim labels (FR1)

**Given** active authority
**When** it opens
**Then** it shows live session consent grants from the Story 10.1 query frame (never persisted) and standing authority rows with their provenance (`granted`, `grandfathered`, `seeded`), clearly distinguished (FR12)
**And** revoking standing authority runs `authority.revoke` with its step-up

**Given** approval history
**When** it opens
**Then** it lists resolved Needs-you items with kind, answer, which surface answered and when

**Given** sign-ins and devices
**When** they are listed
**Then** each device session shows name, created and last-used times, and revocation from Security requires a signed tap and uses the Story 5.11 API

**Given** the audit trail
**When** it opens
**Then** it shows the hash-chained `audit_log` security evidence together with a chain-verification result (NFR31)

**Given** voice-worker tokens
**When** Epic 12 lands
**Then** Security lists them as rotatable and revocable; this station provides the section that Epic 12 fills

## Epic 11: Alerts on your phone and the briefing

Every Needs-you item also arrives by Web Push beside Telegram. At home a tap opens the item; away from home a tap shows a cached, metadata-only summary. When the owner opens the Bridge, a short briefing says what changed since they last looked.

Owner decision (2026-09-13): during quiet hours, high-intensity items push and alert at once; normal-intensity items reach Telegram silently, and their Web Push waits until quiet hours end.

### Story 11.1: Push keys and subscriptions

As the owner,
I want my signed-in devices subscribed to push notifications from my own install,
So that alerts reach my phone without any third party reading them.

**Acceptance Criteria:**

**Given** the Bridge starts without VAPID keys
**When** keys are created
**Then** they are stored in the platform secret store and added to the Story 5.13 backup set (AD-39)

**Given** a signed-in device
**When** it subscribes to push
**Then** the subscription row belongs to that device session, and revoking the device deletes it through the Story 5.11 revocation API (AD-19)

**Given** a subscription endpoint
**When** it is registered
**Then** it must be `https`, and it is refused when it resolves to a loopback, private or link-local address (NFR29)

**Given** the push sender
**When** it is built
**Then** it uses standard VAPID with encrypted payloads, through any maintained library such as `pywebpush` (AD-19, C35)

### Story 11.2: Every Needs-you item also arrives by Web Push

As the owner,
I want every item that needs me pushed to my phone as well as to Telegram, and updated when it resolves,
So that no alert depends on a single delivery path.

**Acceptance Criteria:**

**Given** `needs_you.opened` and `needs_you.resolved` from fan-out of both processes
**When** the gateway notifier receives them
**Then** one notifier owns both Telegram and Web Push dispatch, and Epic 3's Telegram delivery moves into it with no behaviour change (AD-19, FR21)

**Given** an opened item
**When** it is pushed
**Then** `approval`, `question`, `incident` and `alert` go to every subscribed device, `device` goes to signed-in devices only, and Telegram delivery continues per Story 3.6 (FR22)

**Given** a push payload or lock-screen text
**When** it is built
**Then** it carries only item id, kind, intensity and the narrator's `public` rendering (kind and count), and never item content (FR26, NFR34)

**Given** a resolved item
**When** the notifier handles it
**Then** the push notification is replaced using the item id as its tag, and the Telegram message is edited (FR23)

**Given** quiet hours
**When** a `high`-intensity item opens
**Then** it pushes and alerts at once on both paths
**And** a `normal`-intensity item reaches Telegram silently, and its Web Push is sent when quiet hours end, only if the item is still open

**Given** push delivery fails
**When** the failure is recorded
**Then** the notifier's health contributor degrades with a remedy, and Telegram delivery is unaffected (NFR17)

### Story 11.3: Tap a notification at home or away

As the owner,
I want tapping an alert to take me straight to the item at home, and to something useful when I am away,
So that a notification never dead-ends on an error page.

**Acceptance Criteria:**

**Given** a push notification on a device at home
**When** the owner taps it
**Then** the Bridge opens straight on that item's card, by item id (FR24, UX-DR24)

**Given** a push notification on a phone away from the home network
**When** the owner taps it
**Then** the service worker shows the cached metadata-only summary of the item with "open at home" and a "continue in Telegram" link, where approvals can be answered, and no error page (FR25, UX-DR36)

**Given** the service-worker cache
**When** it is inspected by an automated test
**Then** it holds only the push metadata, never item content (NFR34)

**Given** iOS
**When** notifications are used
**Then** they work through the installed home-screen app

**Given** an item that resolved before the tap
**When** the owner taps its notification
**Then** the Bridge (or the away summary, as far as it knows) shows the item as resolved with its outcome

### Story 11.4: What changed since you last looked

As the owner,
I want a short briefing of what changed since I last looked whenever I open the Bridge,
So that I catch up in seconds instead of scanning logs.

**Acceptance Criteria:**

**Given** no last-looked marker store exists
**When** the platform migrates
**Then** an idempotent migration creates a per-owner last-looked marker, stored per owner, not per device

**Given** the owner opens the Bridge
**When** events exist after the marker
**Then** a briefing is built from journal events since the marker, classified by the attention policy and rendered by the narrator: runs, failures, heals, unhealed or paused work, finished work and waiting approvals (FR28, AD-5, AD-30)
**And** it appears on screen first, as a short summary, not a list of every event

**Given** the briefing has been seen
**When** the owner dismisses it or it has been shown
**Then** the marker advances to the briefing's cursor, so the same changes are not briefed again on any device

**Given** nothing changed since the marker
**When** the Bridge opens
**Then** no briefing is shown

**Given** events since the marker that were already pruned
**When** the briefing is built
**Then** it says the window starts at retention, and never invents what happened before

### Story 11.5: The briefing lights up the ship as it is read

As the owner,
I want each thing the briefing names to light up on the Dial and in the log,
So that I can see where it happened while I read about it.

**Acceptance Criteria:**

**Given** a briefing on screen
**When** each named item is shown
**Then** its Dial element and its Ship's log line light in step with the text, using the record each item references (UX-DR30)

**Given** low-power 2D mode or reduced motion
**When** the briefing plays
**Then** the named elements highlight statically instead of animating

**Given** a named item whose target is gone
**When** it is highlighted
**Then** the log line shows `expired`, and nothing is invented on the Dial

**Given** Epic 11
**When** it ships
**Then** no "tap to hear" control is shown; spoken briefings arrive with voice in Epic 13

## Epic 12: Talk to Owl — push-to-talk

The owner talks to Owl with push-to-talk on every device and hardware tier. Speech runs in a separate voice worker, on the platform's own box or another home machine. Every install measures its own host with the built-in `stackowl voice check` (spikes S1, S3, S4 and S8) and states its tier and cost honestly. The owner's reference run on this box sets the defaults. Each owl has a distinct voice, a non-spoken acknowledgement arrives within about 300 ms, and transcripts appear live.

Owner decisions (2026-09-13): voice checks are built into the platform, not throwaway kits. Speech models may run on the Jetson dev box.

### Story 12.1: Speech engines are downloaded safely

As the owner,
I want every speech engine, voice and model the platform downloads to be verified before use,
So that voice never brings tampered files or unsafe model formats onto my box.

**Acceptance Criteria:**

**Given** a download manifest
**When** speech engines, voices and weights are declared
**Then** each entry carries an immutable revision URL and a SHA-256, and a tripwire fails any runtime download not declared there (AD-25, NFR37)

**Given** a runtime download of an engine, voice or weight
**When** it completes
**Then** the downloader verifies the SHA-256 before an atomic rename into `StackowlHome`, and weights are accepted only as safetensors, ONNX or GGUF, never pickle

**Given** NVIDIA speech models
**When** the host has NVIDIA hardware
**Then** they are candidate engines like any other, downloaded through the verified downloader and measured by the voice check (C35)

**Given** the Piper engine
**When** this story lands
**Then** it stays available as a candidate engine installed through the verified downloader, and the default TTS is whichever engine the voice check selects (C35)

**Given** heavy speech dependencies
**When** the platform starts and voice is enabled
**Then** the declared engines install themselves automatically through the verified downloader, with no manual steps, and a failed install degrades voice health with a remedy

### Story 12.2: The voice worker runs speech in its own process

As the owner,
I want speech to run in its own supervised process, on this box or another home machine,
So that heavy audio work never slows or crashes the platform, and never gains any authority.

**Acceptance Criteria:**

**Given** voice is enabled
**When** the gateway starts
**Then** it supervises a voice worker process as a `SupervisedTask`, the worker uses the existing `media/stt` and `media/tts` selectors directly, and it restarts on crash with a health contributor (AD-22, AD-23, NFR17)

**Given** the worker
**When** it runs
**Then** it never connects to `core.sock`, and it runs as the same OS user as gateway and core, with the AD-41 residual risk stated (NFR30)

**Given** a worker token
**When** it is issued
**Then** it is scoped to transcribing and speaking only, is rotatable and revocable, appears in the Security station's voice-worker section (Story 10.7), and issuing or revoking it is written to `audit_log` (NFR28, NFR31)

**Given** a worker on another home machine
**When** it connects
**Then** it authenticates with mutual TLS, is health-checked by the gateway, and is refused if its token is revoked (FR54)

**Given** the existing batch speech path
**When** the worker is introduced
**Then** TUI and Telegram voice capture keep working on the batch STT selector, proven by regression tests (FR58)

### Story 12.3: One voice state machine and the voice channel

As the owner,
I want one server-side voice state machine that drives both what I hear and what the owl mark shows,
So that the screen and the speech never disagree.

**Acceptance Criteria:**

**Given** the gateway voice channel adapter
**When** a voice session starts
**Then** it joins the owner's one conversation through the channel contract, and its transcripts enter with requester kind `voice-unverified` (AD-22, AD-27, FR86)

**Given** the voice state machine in `voice/` (idle, listening, thinking, speaking, interrupted, mic-live)
**When** its state changes
**Then** audio and the Story 6.4 presence mark follow the same state, carried as `voice` envelope messages that are never journaled (FR51, AD-32, NFR34)

**Given** WebRTC signalling on the Bridge origin
**When** a device starts a voice session
**Then** a per-session voice ticket bound to that device session is minted, and a transcript frame without a live ticket is dropped and opens an `incident` Needs-you item (AD-23)

**Given** the device is revoked
**When** revocation happens
**Then** its voice session closes within one heartbeat interval, through the Story 5.11 API (NFR11)

**Given** new voice IPC frames
**When** they are added
**Then** they live in `ipc/frames.py` with `extra=forbid`, and `protocol_version` is bumped (AD-33)

### Story 12.4: `stackowl voice check` measures this host's speed

As the owner,
I want a built-in check that measures how fast voice can respond on my host and picks the right tier,
So that every install knows its honest voice capability from its own hardware.

**Acceptance Criteria:**

**Given** `stackowl voice check` (also runnable from Engineering)
**When** it runs on any install
**Then** it measures, first with a stub LLM and then with the real pipeline:
- the time from detected end of turn to the non-spoken acknowledgement;
- the time from end of speech to first audio.

It records P50 and P95 per engine combination (S1, NFR1, NFR2)

**Given** the capability probe lifted from `media/image/capability.py` and `sandbox/capability.py` into one shared probe
**When** the check runs
**Then** it probes the host (GPU, Apple Silicon, CPU-only, Jetson-class), selects the best tier within the bounds, then force-degrades to prove the fallback states its reason (S8, NFR19)

**Given** the platform running on the same host
**When** the check exercises each engine combination
**Then** it records the voice worker's peak memory alongside the host's available memory and the gateway memory budget (NFR48)
**And** a tier whose peak memory would breach available memory or that budget is refused, with the reason stated, even when its latency passes

**Given** check results
**When** they are stored
**Then** they persist in a migration-created SQLite table with host fingerprint, engines, measurements and chosen tier, and they are shown in Engineering

**Given** a tier over its latency bound
**When** the check reports it
**Then** that tier is marked push-to-talk only, with the reason stated (S1 fail branch)

**Given** the check's Pipecat comparison
**When** the reference run shows Pipecat within bounds and adding value
**Then** the verdict may adopt it; otherwise the worker keeps driving the selectors directly (S1 fail branch)

### Story 12.5: `stackowl voice check` measures accuracy and voices

As the owner,
I want the check to measure how well my host hears me and how quickly it speaks, and let me choose voices blind,
So that the default engines and voices come from my ears and my data.

**Acceptance Criteria:**

**Given** a bundled English phrase set, the owner's own recorded phrases (up to 100, optional) and 20 silence and noise clips
**When** the STT part of the check runs
**Then** it reports word error rate per engine against ≤ 8% on GPU tiers and ≤ 12% on CPU tiers, zero hallucinated text on silence and noise, and partial-transcript latency (S3, NFR5)

**Given** the TTS candidates in the manifest
**When** the TTS part runs
**Then** it measures first audio per engine against ≤ 250 ms GPU, ≤ 600 ms Mac and ≤ 1 s CPU (S4, NFR6)

**Given** a blind ranking page in the Bridge
**When** the owner listens to unlabelled samples
**Then** they rank them, and the result records their top two engines

**Given** a tier with no STT engine within bound
**When** the check reports it
**Then** that tier is push-to-talk only; a tier with no passing TTS uses the best passing engine, NVIDIA models included on NVIDIA hardware (S3, S4 fail branches)

**Given** owner recordings
**When** they are stored
**Then** they stay under `StackowlHome`, are never journaled, and can be deleted by the owner

### Story 12.6: The reference run sets the defaults

As the owner,
I want my run of the voice check on this box to become the reference that sets the default engines, voices and thresholds,
So that fresh clones start from measured defaults, and every other install still measures itself.

**Acceptance Criteria:**

**Given** the checks from Stories 12.4 and 12.5
**When** the owner runs the verdict command after a reference run
**Then** it writes `docs/agentic-os-dashboard/spikes/epic-12-voice-verdict.md` with:
- the measurements per tier;
- a verdict per spike (S1, S3, S4, S8);
- the fail branches applied;
- the proposed default engines, voices, tier thresholds and Pipecat decision.

**Given** a verdict with defaults
**When** it is written
**Then** it appends one entry to `_bmad-output/implementation-artifacts/deferred-work.md` naming the settings defaults to change, so `bmad-loop sweep` applies them (gates AD-22, AD-23, AD-25, AD-32; FR53)

**Given** the checks
**When** this story completes
**Then** they remain in the platform as built-in checks, and are not retired

**Given** the agent work is committed
**When** the story finalises
**Then** its spec is set to `status: awaiting-operator`, and `operator_actions` lists:
1. Run `stackowl voice check` on this box, including the recorded phrases and the blind ranking.
2. Run the verdict command.
3. Commit the verdict document and the deferred-work entry to main.
4. Run `bmad-loop confirm` for this story.

### Story 12.7: The host picks its voice tier honestly

As the owner,
I want voice to use the best tier my host measured, and to tell me plainly when it falls back,
So that I never get silent failures or unexplained slowness.

**Acceptance Criteria:**

**Given** stored check results for this host
**When** a voice session starts
**Then** the tier from the latest check is used, and the Bridge shows the tier and its cost (FR53)

**Given** no check has run on this host
**When** voice is first used
**Then** the platform runs the quick probe, uses the lowest safe tier until a full check completes, and says so

**Given** a missed latency budget or a WebRTC failure during a session
**When** it is detected
**Then** voice falls back to push-to-talk, and the reason is stated on screen (UX-DR32)

**Given** tier selection
**When** backends are considered
**Then** the probe never selects a cloud backend, browser cloud speech APIs are banned, and cloud TTS is used only when the owner has set it explicitly, with its egress shown (NFR35)

**Given** hardware that changes (for example a GPU removed)
**When** the next check or quick probe runs
**Then** the tier follows the hardware, never silently

### Story 12.8: Push-to-talk in the Bridge

As the owner,
I want to hold a button and talk to Owl on any device,
So that I can speak instead of type wherever I am in the Bridge.

**Acceptance Criteria:**

**Given** a signed-in device
**When** the owner starts push-to-talk
**Then** audio flows over WebRTC with host candidates only and no third-party STUN or TURN, and on iOS audio starts only after the gesture (AD-23, NFR28)

**Given** audio is being captured
**When** push-to-talk is held
**Then** the mic-live indicator shows, and the captions bar shows the owner's live partial, then final, transcript in `--cream-dim` italics (FR39, UX-DR11)

**Given** a final transcript
**When** push-to-talk is released
**Then** it auto-sends into the conversation (FR40)

**Given** a spoken order
**When** it reaches the gate as `voice-unverified`
**Then** a reversible, non-consequential order runs at once with undo; a CONSEQUENTIAL order gets Owl's read-back and then needs a signed tap or Telegram approval; an irreversible or owl-authority action is never approved by voice (FR41)

**Given** push-to-talk on every surface and tier
**When** it is tested
**Then** it works on the phone PWA, a phone tab and desktop, and on every tier the check selected (FR38)

### Story 12.9: Owl acknowledges at once and speaks at milestones

As the owner,
I want an instant sign that Owl heard me, a quick "on it", and then speech only when something meaningful happens,
So that voice feels responsive without Owl narrating every step.

**Acceptance Criteria:**

**Given** detected end of turn
**When** it happens on any tier
**Then** within about 300 ms the owl mark switches to thinking and the soft acknowledgement sound plays, produced locally, never by a model call (FR42, NFR1, AD-32)

**Given** a turn that will take time
**When** it starts
**Then** a spoken "on it" follows as fast as the hardware allows, under 1 s on GPU and Mac tiers, through the normal pipeline (FR43, NFR3); Epic 13's S2 check decides whether a separate fast path is added

**Given** progress events that carry only step name, index and total
**When** milestone speech is built
**Then** typed milestone events with meaningful, metadata-only attrs are added to the registry with narrations, and Owl speaks only at those milestones

**Given** thinking is never disabled to meet a budget
**When** latency is tuned
**Then** no change disables model thinking (NFR4)

### Story 12.10: A backgrounded phone never keeps listening

As the owner,
I want voice to stop cleanly when I switch away on my phone, and not lose Owl's reply,
So that nothing listens in the background and nothing Owl said goes missing.

**Acceptance Criteria:**

**Given** a voice session on a phone
**When** the page is hidden or backgrounded
**Then** the server voice session ends, the mic indicator goes dark, ambient cues fall silent, and any unspoken reply is delivered as text in Comms (FR50, AD-23)

**Given** the owner reopens the page
**When** it becomes visible
**Then** push-to-talk is ready again, and a new session starts only on the owner's gesture

**Given** the Telegram voice note and TUI recorder paths
**When** the full test suite runs
**Then** their batch STT regression tests pass (FR58)

### Story 12.11: Each owl has its own voice

As the owner,
I want every owl to sound distinct, with Owl the most recognisable,
So that I always know who is speaking without looking.

**Acceptance Criteria:**

**Given** the available voices, with defaults from the reference verdict
**When** voices are assigned
**Then** the host gets the most distinctive voice, each crew owl gets a distinct bundled voice, and no voice is ever cloned (FR55)

**Given** more owls than bundled voices
**When** a new owl is created
**Then** a deterministic assignment rule gives it a voice (for example the least-used voice with a pitch or rate variation), recorded on the owl, and the owner can change it through a command

**Given** a voice assignment change
**When** it is made
**Then** it runs through the one door with undo

**Given** an owl speaks
**When** its reply is voiced
**Then** it uses its assigned voice, and the captions show which owl is speaking

## Epic 13: Hands-free conversation — and the Bridge replaces today's dashboard

The epic covers:
- **Hands-free conversation:** an open mic, where Owl's speech pauses when the owner talks.
- **Understanding interruptions:** Owl tells stop, steer, a new question and a correction apart, and undoes misheard orders automatically.
- **Proactive speech and the spoken briefing.**

The built-in voice check grows to cover spikes S2, S6 and S9, and a device check page in the Bridge covers S5 and S7 on the owner's phones.

The last story ships the Bridge as the complete replacement for today's dashboard and deletes `control_plane`. It runs only once Epics 1–12 and Stories 13.4–13.8 are finished, whether their spikes passed or took their binding fail branches.

### Story 13.1: `stackowl voice check` covers turn-taking

As the owner,
I want the built-in voice check to measure first answers, end-of-turn detection and interruptions on my host,
So that hands-free is offered only where it actually works.

**Acceptance Criteria:**

**Given** 30 bundled voice prompts (chit-chat and tool turns)
**When** the turn-taking check runs
**Then** it records first answer token (bound: ≤ 700 ms for chit-chat) and first progress for tool turns (bound: ≤ 1 s), and flags chit-chat over 2 s as the S2 failure (NFR4)

**Given** 50 bundled hesitant utterances
**When** end-of-turn detection is checked
**Then** it reports premature cut-offs (bound ≤ 5%, failure > 10%) and detection delay (bound ≤ 400 ms) per tier (S6, NFR8)

**Given** scripted speech over Owl during speech, during generation and during a running tool, using stop, steer, new question, transcript correction, backchannel and noise utterances
**When** the interruption check runs
**Then** it verifies that:
- speech pauses at once;
- Owl's understanding classifies each utterance with no word list;
- stop, steer, new question and correction drop the paused speech;
- a correction is answered fresh;
- backchannel and noise resume the speech;
- there is no orphan audio and no duplicate task (S9).

**Given** results
**When** they are stored
**Then** they join the Story 12.4 check results per host, and show in Engineering

### Story 13.2: A device check page for barge-in and the installed app

As the owner,
I want a page in the Bridge that tests barge-in and the installed app on each of my devices,
So that phone voice is proven on my real phones, and can be re-checked after each iOS release.

**Acceptance Criteria:**

**Given** the device check page on an iPhone, an Android phone and a laptop
**When** the barge-in check runs
**Then** it measures pause latency (bound ≤ 300 ms in at least 90% of cases), false barge-ins (at most one per 10 minutes of Owl speech, cue audio included) and whether paused speech survives a backchannel, comparing the WebRTC and WebSocket transports (S5, NFR7)
**And** it confirms no earpiece routing on iOS

**Given** the installed PWA on a phone
**When** the lifecycle check runs
**Then** it records a 10-minute session with no permission re-prompt, lock and app switch, headset connect and disconnect, and close-and-reopen with a working microphone, plus announced pause and resume (S7, NFR9)
**And** after backgrounding, ambient cues fall silent, hands-free shows as paused, and one tap resumes

**Given** each device run
**When** it completes
**Then** results are stored per device class and are visible in Engineering, and the page stays in the platform for re-runs after major iOS releases

### Story 13.3: Turn-taking verdicts set the voice rules

As the owner,
I want my turn-taking results turned into rules for each tier and device,
So that hands-free, barge-in and interruptions are enabled only where they proved themselves.

**Acceptance Criteria:**

**Given** results from Stories 13.1 and 13.2
**When** the owner runs the verdict command
**Then** it writes `docs/agentic-os-dashboard/spikes/epic-13-turn-taking-verdict.md` with a verdict per spike (S2, S5, S6, S7, S9) per tier or device class, and the resulting rules:
- whether a separate fast "on it" path is needed;
- hands-free viability per tier;
- the chosen transport.

**Given** failing results
**When** the verdict is written
**Then** it names the binding fail branches:
- S2: add a fast "on it" path.
- S5 or S6: push-to-talk only on that tier until a turn detector passes.
- S7: phone voice is foreground-only, with a tap to start each session.
- S9: barge-in only pauses speech; stop, steer and correction go through on-screen controls.

**Given** rules that change defaults
**When** the verdict is written
**Then** it appends entries to `_bmad-output/implementation-artifacts/deferred-work.md` for `bmad-loop sweep` (gates AD-23, AD-27, AD-31, AD-32)

**Given** the agent work is committed
**When** the story finalises
**Then** its spec is set to `status: awaiting-operator`, and `operator_actions` lists:
1. Run the turn-taking check on this box.
2. Run the device check page on an iPhone, an Android phone and a laptop.
3. Run the verdict command.
4. Commit the verdict and its deferred-work entries.
5. Run `bmad-loop confirm` for this story.

### Story 13.4: Hands-free mode

As the owner,
I want to switch on hands-free and just talk,
So that I can converse with Owl without touching the screen.

**Acceptance Criteria:**

**Given** a tier and device class where the Story 13.3 verdict allows hands-free
**When** the owner turns on the hands-free toggle
**Then** the mic opens with voice-activity detection and end-of-turn detection, with no wake word, English only (FR38)

**Given** a tier or device class where hands-free did not pass
**When** the owner looks for the toggle
**Then** it is unavailable, and the reason is stated, with push-to-talk still available (UX-DR32)

**Given** a slow tier that passed with a warning
**When** hands-free is on
**Then** an honest slow-tier warning is shown

**Given** hands-free is on
**When** audio is captured
**Then** the mic-live indicator shows at all times, and captions show the owner's live transcript

**Given** the phone is backgrounded during hands-free
**When** that happens
**Then** hands-free shows as paused, the server session ends, and the Story 12.10 behaviour applies (FR50)

### Story 13.5: Owl pauses when you talk, and resumes after "mm-hm"

As the owner,
I want Owl to stop talking the moment I start, without stopping its work, and to carry on after a simple "mm-hm",
So that talking to Owl feels like talking to a person.

**Acceptance Criteria:**

**Given** Owl is speaking
**When** the owner starts to speak
**Then** Owl's speech pauses within 300 ms in at least 90% of cases, the presence mark collapses when audio is flushed, and the running task is never paused or stopped by the speech alone (FR44, FR86, NFR7)

**Given** the owner's utterance is a backchannel or noise
**When** Owl's understanding classifies it
**Then** the paused speech resumes where it left off (FR46)

**Given** Owl's own voice, ambient cues and the acknowledgement sound
**When** they play during a voice session
**Then** they play through the voice peer connection, so echo cancellation covers them, and they never trigger barge-in (FR52, Spine UI: sound)

**Given** false barge-ins
**When** they are measured in a session
**Then** there is at most one per 10 minutes of Owl speech on a passing tier

### Story 13.6: Owl understands stop, steer, a new question or a correction

As the owner,
I want Owl to understand what I meant when I talk over it, not match keywords,
So that "stop", a new direction, a new question and "no, I said…" each do the right thing in any wording.

**Acceptance Criteria:**

**Given** an utterance spoken over Owl
**When** it is processed
**Then** Owl's normal understanding decides whether it is a stop, a steer, a new question, a correction, a backchannel or noise, never a keyword or word list (FR45)
**And** a behavioural test proves the classification goes through Owl's understanding call, and that each interruption kind is recognised across several paraphrased wordings, not only a fixed phrase

**Given** a stop, steer, new question or correction
**When** it is decided
**Then** it maps to a typed command submitted through the one door with requester kind `voice-unverified` and the utterance's `utterance_id`, and the paused speech is dropped (FR46, AD-32)

**Given** a correction ("no, I said…")
**When** it is applied
**Then** Owl answers the corrected sentence fresh, and never continues an answer to words the owner did not say (FR47)

**Given** a tier where S9 did not pass
**When** a voice session is open
**Then** on-screen stop, steer and correction controls are shown, and barge-in only pauses speech (UX-DR32)

### Story 13.7: A misheard order is undone automatically

As the owner,
I want a spoken correction to undo whatever Owl did on the misheard words before doing what I actually said,
So that a transcription mistake never leaves a wrong change behind.

**Acceptance Criteria:**

**Given** a misheard order that already ran reversible commands
**When** the owner's correction is understood
**Then** those commands are found by the original `utterance_id` in the task store and undone first, through their declared undo commands (FR48, AD-27)

**Given** the undo applied
**When** it completes
**Then** the card shows the automatic undo, then the corrected action (UX-DR31)

**Given** a misheard order that triggered an irreversible or consequential action
**When** the correction arrives
**Then** nothing irreversible ran without step-up (voice cannot approve it), so there is nothing to undo, and the pending approval item for it is withdrawn

**Given** an undo that is no longer available (superseded or past the window)
**When** the correction arrives
**Then** Owl says so, shows `{reason, remedy}` on the card, and does not run the corrected order blindly over it

### Story 13.8: Proactive speech and the spoken briefing

As the owner,
I want Owl to speak up only about things worth telling me, and to read me the briefing,
So that the Bridge feels alive without ever becoming noisy.

**Acceptance Criteria:**

**Given** an event that would notify the owner anyway (attention `needs_you`)
**When** the Bridge is open and visible
**Then** Owl may speak it proactively, and the owner can mute proactive speech in one control (FR49, AD-5)
**And** nothing is spoken proactively while the Bridge is hidden or closed

**Given** the Story 11.4 briefing
**When** the owner opens the Bridge
**Then** a "tap to hear" control speaks it after the first tap, or it plays at once if hands-free is on, with each named item lighting in step (FR29, UX-DR30)

**Given** a backgrounded phone reopened
**When** the owner taps once
**Then** hands-free resumes and the spoken briefing starts (FR50)

**Given** the Story 13.3 verdict requires a fast "on it" path (S2 failed)
**When** this story lands
**Then** a separate fast "on it" path is added, without disabling model thinking (FR43, NFR4)

### Story 13.9: The Bridge replaces today's dashboard

As the owner,
I want the old dashboard removed completely in the same change that makes the Bridge the platform's only dashboard,
So that there is one secure front door and no dead code left behind.

**Acceptance Criteria:**

**Given** the old dashboard's eight read views (health, schedules, config, skills, tasks, agents, memory, interactions)
**When** this story starts
**Then** each maps to a named Bridge view (Engineering, Missions, Crew or Archives), and the story finalises `blocked` if any view has no Bridge equivalent, so nothing the old dashboard showed is lost

**Given** Epics 1–12 and Stories 13.4–13.8 are finished
**When** this story runs
**Then** `src/stackowl/control_plane/` is deleted with its registration in the startup orchestrator, its tests, `ICON_SVG` and its guard (FR87, AD-7, AD-40)

**Given** `stackowl control-plane reset-password`, the control_plane password hash and `config/control_plane_password_migration.py`
**When** this story lands
**Then** they are deleted, not relocated

**Given** existing installs with a `control_plane` settings section
**When** the platform upgrades
**Then** an idempotent config migration moves any still-meaningful settings into the `bridge` section and removes the old section, and running it twice changes nothing

**Given** the reachability probe and census, the notification deliverer and every other reference to `control_plane`
**When** the deletion lands
**Then** they are repointed to the Bridge or deleted in the same change, and no import or string reference to `control_plane` remains in `src/`, except the config migration module and its test named in this story, which must read the old `control_plane` section

**Given** control_plane's page-constant, no-framework and no-CDN guards
**When** this story lands
**Then** they are retired with it, while its auth invariants remain as `bridge/` tripwires (from Story 5.8)

**Given** `logo/stackowl-mark.svg` and `logo/stackowl-logo.svg`
**When** the build ships
**Then** a drift tripwire asserts every shipped copy of the mark (the inline mark, PWA icons, build assets) draws the same path, and they are the single source of the mark (AD-40)

**Given** the old dashboard address (port 8787)
**When** anything requests it after the change
**Then** nothing listens there, with no redirect and no notice

**Given** the change is complete
**When** it is verified
**Then** `./scripts/tripwires.sh` passes, the full test suite passes when run detached, the platform is restarted with `./start.sh`, and the Bridge is verified live on this box with the owner's devices
