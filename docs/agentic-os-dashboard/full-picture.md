# The StackOwl Bridge: the full picture

**For** Boss. **Approved** by Boss on 2026-09-12. **Date** 2026-09-12. **Status:** approved decision document; nothing is built. Product decisions come from Q1–Q52 and L1–L4 (§12). Anything else is marked *proposal*, *finding* or *open question*.

**Sources:** [01] `docs/research/agentic-os-dashboard/01-agentic-os-and-jarvis-interface.md`, [02] `…/02-platform-surface-inventory.md`, [03] `…/03-voice-conversation-spike.md`. Counts from [02] are a live snapshot of this box on 2026-09-12.

---

## 1. The picture in one page

*One illustrative day; owls, messages and numbers are sample content.*

**07:40, phone.** A notification: "Owl: a job needs you." He taps it and the phone opens straight on that item. Behind it sits the compact viewscreen, dark and quiet, with the Needs-you strip on top and Comms one swipe away. The owl mark breathes slowly, in step with the platform's real heartbeat; ordinary activity moves in cream light only. The strip holds two items, the only things on screen in the accent colour. The brighter one is the item he opened: a job that failed five times in a row, which Owl could not heal, so Owl paused it; undo is on its card. The other is an approval.

The briefing is already on screen. His first tap starts Owl speaking it, about twenty seconds, because the phone will not play audio before a tap [03 §5.3]: *"Since 23:10: forty-one scheduled runs, six failures. The backup failed once and I healed it. One job failed five times and I could not heal it, so I paused it. The research owl finished your report. One approval is waiting."* As each item is named, it lights on the viewscreen. The healed backup never entered the strip; it lives in its record.

The research owl wants to post the report to a Slack channel. He holds the talk button: "Who will see it?" Owl answers. "Post it." The owl asked, so his spoken yes counts only after read-back; a posted message cannot be taken back, and he never set Owl up to post on its own, so Owl reads back exactly what will happen (channel, text, attachment) and a confirm button appears. He taps it; the approval leaves the strip. The paused job stays, still bright, until he deals with it.

He pockets the phone. Mobile browsers cut the mic when a page is backgrounded [03 §5.3], so the mic indicator goes dark and the session shows as paused; reopening the page will take one tap, which also starts the spoken briefing. On the train he types a follow-up in Telegram: same conversation, same mind.

**21:00, desktop.** The full bridge: Viewscreen in the centre, six stations around it, the Needs-you strip on top. The ship is calm. Two owls show small real motion in cream light, one mid-task, one on a scheduled run; the paused job is still the only thing in the accent colour. A short pulse crosses from Comms to an owl; he clicks it and a card shows the cause: a Slack message routed to that owl at 20:58.

Hands-free is on at his desk. He asks aloud, "Why did that job keep failing?" The moment Owl detects he has finished, the owl mark switches to thinking with the soft acknowledgement sound, Owl says "on it" within a second, and Engineering opens on the self-healing incident: what failed, what Owl tried, why no heal held. The backup failure Owl healed overnight sits in the same record, never in the strip. An ambient cue, very soft texture for an ordinary event, dips under Owl's voice. In Missions he retries two dead-lettered tasks; both pass the same consent and authority as a Telegram request.

He sets a long job: "Have the research owl compare the three firmware options and shortlist them." Starting a task can be undone and the order is his own, so it runs at once with no read-back; Owl says so, puts undo on the task's card, then speaks only at milestones. Midway through a milestone he cuts in: "Include the older model." Owl's speech pauses the moment he starts talking. No keyword decides what happens next: Owl understands the words as a steer, drops the rest of the milestone, and the task carries on with the change. Had he said he no longer wanted the comparison, the same understanding would have stopped the task; had the transcript misheard him and he said "no, I said…", Owl would have dropped the paused speech, applied the correction and answered the corrected sentence fresh; had he only murmured "mm-hm", Owl would have resumed where it left off.

Before closing he opens the flight recorder in Archives and replays last night as a thirty-second time-lapse: every run, every failure, the job Owl paused, exactly as recorded. Nothing is invented.

---

## 2. Principles

1. **Truthful motion.** Every mover is caused by a real event and opens that event when tapped. Idle breathing follows the server heartbeat, never the browser's render loop; a stale stream visibly stops breathing and shows the age of the last event. Sampling is declared. Decoration is allowed only where it cannot be read as data (the "placebo HUD" warning, [01 §3.3, §4.3]).
2. **Dark-cockpit attention.** Healthy is calm, and normal activity moves in cream light only. The accent colour appears only when something needs the owner, and is never diluted: a failure Owl could not heal becomes a Needs-you item at higher intensity, while a failure Owl healed shows only in its record (Q34). Strong motion and the Needs-you alert, the only sound designed to grab attention and the sound twin of the accent colour, belong to what needs the owner; severity sets intensity, not activity volume [01 §3.4]. Ambient cues are very soft texture for ordinary events and must never compete with that alert (Q51). This principle governs attention; the acknowledgement sound (Q39) answers something the owner just did, so it is feedback and belongs to neither class.
3. **No back door.** Every web action passes the same consent, authority and audit as any other surface.
4. **One mind, many surfaces.** One conversation, memory and task loop everywhere. Web actions become tasks in the existing loop, never a second engine [02 hard fact 14].
5. **Text never lives only in the canvas.** Every drawn entity has a DOM/ARIA twin that is also the keyboard path; moving content has a visible pause control (WCAG 2.2.2) [01 §6.5].
6. **Permissive licences only:** MIT, Apache, BSD; CC-BY for weights. The default install contains nothing else. NVIDIA-licensed models are never bundled; on NVIDIA hardware the owner may choose to download one after its licence is shown (Q44).
7. **Self-hosted.** No feature requires a third-party service; browser cloud speech APIs are excluded [03 §5.4]. The one named exception is the free-domain fallback (Q42, Q46): public DNS and a public certificate service are basic internet infrastructure, used only if StackOwl's own certificate authority proves unworkable on stock iPhones (spike B1). Every front-end asset is vendored and served by the platform; nothing loads from a CDN or third-party host (Q43).
8. **Runs on any hardware.** Probe, pick the best tier, state its cost honestly. Fix the platform for a fresh clone, not for this box.
9. **Retired means deleted.** `control_plane` is deleted in the change that ships the bridge, not before; until then it stays, with the Q29 login fix (Q45).

---

## 3. The bridge

**Postures.** Desktop shows the full bridge. The phone opens on the compact viewscreen plus the Needs-you strip, with Comms one swipe away; opening from a notification goes straight to that item (Q8, Q38).

### 3.1 The Viewscreen

The live ship: the crew and what each owl is doing, missions in flight, scheduled jobs approaching their due time (projection, not just state [01 §4.3 rule 8]), comms traffic arriving from channels, and engineering health. Every mover opens its record. The visual grammar is the mockup's job.

*Finding:* none of this exists as a stream today. Tool and model calls, task claims, job runs, consent, health changes, heals, memory writes, delegation hops and deliveries are logged or stored as rows, never evented [02 §3.1]. The bridge must also show the ship as it is: a scheduler that lists 169 enabled jobs although 136 of them are one-shot `rollover_summary` jobs that completed but were never switched off (status `completed`, `enabled=1`, next run stuck in the past), and 33 live schedules, 77 dead-lettered tasks, delegation stopped since 2026-08-24. That is why the last rebuild refused an animated graph (`169d30f8`) [02 hard fact 10]. A quiet or failing ship must look quiet or failing.

### 3.2 The six stations

| Station | You see and do | Fed today by [02] | Missing today [02] |
|---|---|---|---|
| **Comms** | Conversation with Owl; Telegram, Slack, TUI threads; delivery failures | `sessions` (147), `conversations` (1,455), `messages` (4,601), `delivery_attempts`, `undelivered_outbox` (221) | No `web` channel; no transcript browser; cross-channel identity is only an alias map |
| **Crew** | Each owl's activity, authority (bounds ∩ ceiling), skills, DNA; pause, resume, rename, grant | `owls` (11), `owl_dna`, `skill_ownership`, `authz/bounds.py`, `owl_build` (grant is always-ask) | No HTTP action; no activity events |
| **Missions** | Tasks, schedules, retries, dead letters; pause, steer, take over, retry, cancel, run now | `tasks` (1,221), `TaskLoop`, `jobs` (170), `job_runs`, `JobScheduler` pause/resume/run_now | Nothing cancels or retries a task; `steer`/`stop` frames never sent; no finished-task history |
| **Engineering** | Providers and models, health over time, self-healing incidents, cost as fuel, processes | Provider tiers, `HealthAggregator` (15 subsystems), `cost_records` (134,455), incident events in `audit_log` | No time axis (15/15 ok while the sweep logged unhealthy 49 times that day); no incidents table; breakers and governor in core memory only |
| **Archives** | What it knows about the owner, decision ledger, flight recorder | `USER.md` + curated owl files (19), `lessons` (5,968), `reflections`, `turn_decisions` (798), Kuzu (core only) | Decisions upserted per session, so no per-turn history; no event table to replay |
| **Security** | Authority grants, approval history, sign-ins and devices, audit | Consent grants (memory only, never persisted by design), hash-chained `audit_log` (624) | No list of active grants; no web approve/deny; no web actor in audit; no sign-in store |

*Proposal:* the first control actions are the lowest-risk ones, each with a single existing implementation: job pause/resume/run-now, task retry/cancel, owl pause/resume, skill enable/disable, config set, command dry-run (`??`). `/bye`, `/provider`, `/connect`, owl grant, `/memory forget` and `/cost privacy` stay consent-gated [02 §4.3].

### 3.3 The Needs-you strip

One priority-sorted queue on every screen, signalled by the Needs-you alert (Q51) and mirrored as a phone notification. It holds pending consent prompts, clarifying questions (`clarify_ask`), incidents needing a decision, failures Owl could not heal, irreversible actions the owner did not set up beforehand (Q36), new-device approvals (Q41) and budget alerts (`budget_80pct_alert` is already evented), as notify / question / review items [01 §2.1–2.2]. Approvals are mirrored to Telegram, except new-device approvals, which appear only on already signed-in dashboard devices (Q48). A failure Owl could not heal sits at higher intensity; a failure Owl healed never enters the strip and shows only in its record (Q34). *Findings:* pending prompts live in core memory with no web prompter [02 §4.3]; Web Push needs HTTPS and, on iOS, a home-screen install [01 §6.3].

### 3.4 The flight recorder

Replays the bridge from recorded events at any speed. *Finding:* there is no event table; jsonl keeps 30 days but "is a log, not a contract", and `audit_log` is sparse [02 §3.2, §3.5]. The recorder and the opening briefing share one prerequisite: persisted, bounded event history (§8.1).

---

## 4. Owl and the crew

- **Host.** The Secretary is the ship's voice, named "Owl" by default and renamable. Other owls are crew with their own identities and speak when addressed.
- **Anticipation.** Owl acts alone only within authority already granted, shows what it did, offers undo, and turns anything beyond that into a Needs-you request. It takes an irreversible action on its own only when the owner explicitly set that up beforehand, such as a scheduled daily report that sends a message; each run leaves a tappable record. Any other irreversible action goes to Needs-you. When Owl or a crew member asks for a reversible approval, a spoken "yes" counts only after Owl reads the request back; the owner's own spoken orders for reversible actions run at once, with undo (Q36, Q49). In the platform, authority is owl bounds ∩ creation ceiling plus consent grants that die on restart by design [02 hard facts 5–6]. *Finding:* there is no general undo; some actions have an inverse (pause/resume), many do not (a sent message).
- **Briefings.** On opening, a short summary of what changed since the owner last looked, each item lit as it is named. It appears on screen first and is spoken after the owner's first tap (the iOS audio rule [03 §5.3]), or at once if hands-free is already on. After the phone is backgrounded, hands-free counts as paused, so reopening takes one tap, which also starts the spoken briefing. The owner asked for it by opening, so it is not proactive speech (Q37). Needs a per-owner "last looked" marker and the §8.1 history.
- **Voices.** A distinct bundled permissive voice per owl, Owl's the most distinctive; no cloning. *Proposal:* Kokoro-82M (Apache-2.0) voice packs by default, Pocket TTS (CC-BY-4.0) for GPU-less hosts [03 §3].

---

## 5. Voice

**Modes.** Push-to-talk everywhere; hands-free as a toggle (open mic with voice-activity detection, end-of-turn detection and barge-in [03 §4]). No wake word. The conversation is one word away only while hands-free is on (Q37). A mic-live indicator whenever audio is captured [01 §5.2]. Transcripts appear live and keep auto-sending. Because a transcript can be misheard, the owner's own spoken order can at most trigger a reversible action, which runs at once, shown with undo and with no read-back; anything irreversible still needs read-back plus an on-screen tap (Q35, Q49). When the phone is backgrounded the browser cuts the mic [03 §5.3], so hands-free counts as paused; reopening takes one tap, which also starts the spoken briefing (Q37). English only.

**Stop, steer, correct.** No keyword matching. The instant the owner starts to speak, Owl's speech pauses; the utterance then goes through Owl's normal understanding, which decides whether it stops the task, steers it or corrects the last transcript (Q40). If the utterance is a steer, a stop, a new question or a correction, the paused speech is dropped; if it was only a backchannel ("mm-hm") or noise, Owl resumes where it left off (Q47). After a correction ("no, I said…") Owl applies it and answers the corrected sentence fresh; it never continues an answer to words the owner did not say (Q50).

**Turn-taking.** *Proposal:* Silero VAD (MIT) and Smart Turn v3 (BSD-2, ~12 ms on CPU) [03 §4.2]. One server-side state machine drives both audio and presence, so screen and speech never disagree [01 §5.3].

**Long tasks.** Every tier gives a non-spoken acknowledgement within ~300 ms, measured from the moment end of turn is detected, not from the last spoken syllable: the owl mark switches to "thinking" and the soft acknowledgement sound plays. The spoken "on it" follows as fast as the hardware allows, under a second on GPU and Mac (Q39). After that Owl speaks only at meaningful milestones. Speaking over Owl pauses its speech, never the task by itself; Owl's understanding decides whether the words stop or steer the task or correct the transcript (Q40), and so whether the paused speech is dropped or resumed (Q47, Q50). *Findings:* progress chunks carry only step name, index and total, too little for a meaningful milestone [02 §5.2]; the `stop` and `steer` frames exist but are never sent [02 §4.2].

**Orders, approvals and irreversible actions.** Voice can request anything (Q16). The owner's own spoken order for a reversible action runs at once, with undo and no read-back (Q35). When Owl or a crew member asks the owner for something, Owl reads back exactly what will happen before a spoken "yes" counts (Q36). An irreversible action needs that read-back plus an on-screen tap (Q16). Q49 sets this split. Owl acts irreversibly on its own only where the owner set it up beforehand (§4). *Finding:* the platform has no "irreversible" class, only always-ask consent categories and a `destructive` flag on command actions [02 §4.1, §4.3]. If a transcript was misheard and the order already ran, the owner's correction makes Owl undo that action automatically, show the undo on its card, and then carry out what the owner actually said (Q52).

**Proactive speech.** Only about things that would notify the owner anyway, only while the dashboard is open, always mutable. The opening briefing is not proactive speech (Q37).

**Hardware ladder.** The platform probes the host; push-to-talk works on every tier, every tier gives the non-spoken acknowledgement within ~300 ms of detected end of turn (Q39), hands-free warns honestly when slow, and speech can run on another machine on the network. Estimates for a turn with no tools [03 §6.3]:

| Tier | Permissive default stack | Voice-to-voice |
|---|---|---|
| NVIDIA GPU | Parakeet TDT 0.6B v2 (CC-BY) behind VAD → pipeline → Kokoro | 0.6–1.2 s [M] |
| Apple Silicon | MLX Whisper-turbo or Parakeet-MLX → pipeline → Kokoro | 0.8–1.5 s [M] |
| CPU-only / Jetson Orin | Moonshine v2 or sherpa-onnx → pipeline → Pocket TTS or Kokoro | 1.5–4 s [L] |

Nemotron streaming STT (NVIDIA licence) is never bundled; on NVIDIA hardware the owner may choose to download it, and its licence is shown first (Q44). Tool-using turns take seconds to minutes on every tier; StackOwl's own turn, not the speech models, is the biggest latency risk [03 summary 3]. The CPU and Jetson figures are community evidence with conflicting TTS numbers.

**Architecture.** A streaming cascade around the existing pipeline, with no speech-to-speech model, so owls, tools, memory and consent keep working [03 §1.2]. Pipecat (BSD-2) with peer-to-peer `SmallWebRTCTransport`, if the spikes pass; WebRTC playback also gets Chromium echo cancellation, which WebSocket plus Web Audio does not [03 §4.4]. Piper (now GPL-3.0) is replaced. *Finding:* nothing today connects a browser mic to the pipeline or the pipeline to a speaker; STT is batch Whisper `base` and TTS writes a file [02 §5.3].

---

## 6. Look and sound

**Identity.** Dark hull `#0b0e12` and warm cream instrument light `#f4f1ea` from the logo. The mark's stacked rounded blocks (top block with two round eyes and a beak notch, a full bar, a shorter bar) set the grid and panel language. Normal activity moves in cream light only. One accent colour, appearing only when something needs the owner and never diluted (Q34); the mockup proposes which. No Marvel copy; the mark is never redesigned.

**Host presence.** The mark comes alive through light and motion only, driven by the voice and liveness state machine [01 §4.3, §5.2]:
- **idle:** breathes at the server heartbeat
- **listening:** reacts to the owner's own mic level
- **thinking:** visibly different from listening; switching to it within ~300 ms of detected end of turn is the non-spoken acknowledgement (Q39)
- **speaking:** follows Owl's output audio; pauses the instant the owner starts to speak, picks up where it left off after a backchannel or noise, and ends when the paused speech is dropped, including after a transcript correction (Q40, Q47, Q50)
- **needs you:** the accent colour, at higher intensity for a failure Owl could not heal, with its sound twin, the Needs-you alert (Q51)
- **stale or disconnected:** stops breathing and shows the age of the last event

**Sound.** Two classes, both with volume control and respecting silent mode (Q14, Q51). **Ambient cues** are very soft texture for ordinary events, on by default: they never repeat or form a barrage [01 §3.2 #8], duck under Owl's voice, go silent when the phone is backgrounded, and can be switched off on their own. **The Needs-you alert** is the only sound designed to grab attention, the sound twin of the accent colour; principle 2 governs attention, so ambient cues never compete with it. The soft sound that joins the thinking acknowledgement (Q39) answers something the owner just did, so it is feedback and belongs to neither class.

**Rendering.** WebGPU with automatic WebGL 2 fallback for the Viewscreen only; every other screen is lightweight DOM. Battery saver, a low-end device or reduced motion drops to a low-power 2D mode: same truth, less spectacle. *Proposal* [01 §6.4]: render on demand, a 15–30 fps ambient tick with full rate only for event transitions, pause when hidden, and an adaptive quality ladder (GitHub's globe degrades below 55.5 fps). *Finding:* iOS Low Power Mode caps animation at 30 fps and pages cannot detect it. Phone battery and thermal figures are [Low] evidence.

---

## 7. Access and security

**Reachability.** Self-hosted and reachable from anywhere through a built-in WireGuard-based private network with guided setup. Automatic HTTPS on the home network makes the page a secure context, which unlocks microphone, passkeys, service worker, push and PWA install [02 §8]. The certificate comes first from StackOwl's own certificate authority, installed on the phone with guided steps; if spike B1 proves that unworkable on stock iPhones, the fallback is a guided free-domain setup (Q42). Router changes and third-party tunnels are never required; an own domain is optional unless that fallback applies. The fallback's public DNS and public certificate service are the one named exception to principle 7 (Q46).

**Sign-in.** Passkeys plus a one-time recovery code. At setup the platform shows a one-time setup code in its terminal and sends it to the owner's existing Telegram; entering it registers the first passkey. "The owner's Telegram" means the single allowed Telegram user. *Finding:* today that is `telegram_channel.allowed_user_ids` holding exactly one id, resolved by `resolve_owner_addresses` (`notifications/recipient.py:28-64`). With none or several ids, codes are shown only on the terminal/CLI; the bridge's owner record (§8 item 7) formalises this later. A new device is approved only from an already signed-in dashboard device, as a Needs-you item, or with the recovery code; never from Telegram, even though other approvals are mirrored there (Q41, Q48). The setup code is the same ownership proof the current dashboard gains in L1, so the bridge reuses that mechanism rather than inventing a second one.

*Finding:* today one shared admin/admin login on `0.0.0.0` over plain HTTP makes every caller the default principal with every severity; no users, password hashing or session store exist [02 §1.4, §6]. The Q29 fix (L1–L4, §12, in progress separately) changes the login itself: no `admin/admin` and no token for a publicly known password; ownership proven by the setup code plus a new password; the password removed from `stackowl.yaml` and stored once as a salted hash (a custom YAML password is imported once on upgrade); an unreadable store refuses sign-in with a remedy and is reported to self-healing as an incident; and `stackowl control-plane reset-password` on the host issues a fresh setup code without a restart. It does not add HTTPS, per-user principals or severity checks; those stay bridge prerequisites (§8 items 2, 5, 7).

**How a web action flows** (*proposal*, from [02 §4.3]):
1. An authenticated session calls a specific endpoint; there is no generic "run any command" endpoint.
2. The endpoint checks severity with the existing `ControlPrincipal.may()`; `WRITE` and `CONSEQUENTIAL` are declared but unused today.
3. The mutation is enqueued as a task.
4. Consent and authority apply exactly as on Telegram, through a real `web` prompter.
5. The audit log records the web principal as actor.

The test-pinned invariants carry over [02 hard fact 8]: fail closed without a credential, origin before token, per-handler auth (middleware skips WebSocket upgrades), uniform 401, no token in logs, no third-party hosts. The pinned "no framework, no CDN, no build step" is replaced by Q43: a build step and libraries are allowed, every asset is vendored and served by the platform, and a fresh clone gets pre-built assets without needing Node. An offloaded speech machine sits inside the private network and authenticates (*proposal*).

---

## 8. What the platform must gain first

Capabilities, not code. **★ = a platform fix worth making even without the bridge.**

1. **★ A live, typed, persisted event stream.** Tool/model calls, task claim/finish, job runs, consent, health, heals, memory writes, delegation and delivery, emitted at the action site with trace ids into a bounded replayable table, carried core→gateway (`ProgressEventFrame` is defined but never constructed) and resumable by the browser. It also repairs split-mode TUI progress and the broken `publish` bus call [02 §3.1, §3.5]. It feeds the Viewscreen, recorder, briefing and voice milestones.
2. **★ An authorised control path.** `CommandRegistry.dispatch` checks nothing, and the 33 live commands include `/bye`, `/config` and `/provider`. Needed: per-command severity, task-enqueued mutations, web actor in audit [02 §4.1, gaps 7–8].
3. **★ Consent that never auto-grants an unknown channel.** Channels without a registered prompter route to `AutonomousPrompter`, which grants ordinary consequential actions (`tools/consent.py:664-685`); core registers prompters for five names only, so `web` would be auto-granted [02 §4.3].
4. **★ The consent address survives IPC.** `ConsentRequestFrame` has no `reply_target`; in split mode the gateway rebuilds requests without an address and guesses from the session key [02 §4.3].
5. **★ HTTPS and private remote access.** Plain HTTP sends password and token in clear text and blocks mic, push and install [02 §8]; the L1–L4 login fix does not change that. Certificates from StackOwl's own authority, with the guided free-domain fallback (Q42), the one named exception to principle 7 (Q46).
6. **★ A web server that survives core restarts.** The dashboard runs in core and dies on every `os.execv`; the gateway↔core socket takes one peer, so a new query path means a new frame or socket [02 hard facts 2–3].
7. **★ Owner identity.** A real owner record that formalises today's single-allowed-Telegram-user rule (§7), the setup-code ownership proof shared with L1, passkeys, new-device approval from a signed-in dashboard device or the recovery code only (Q48), sessions with expiry and revocation, and web sessions mapped to the owner's principal and identity alias, so the web is the same person as the Telegram owner [02 §6].
8. **A core query path** for in-memory state: active grants, live health, breakers, context windows, worker occupancy [02 gap 10].
9. **★ A time axis:** health, cost, job and task history, including finished rows [02 gap 6].
10. **A `web` conversation channel** that owns the response stream and renders action buttons [02 gap 11].
11. **A streaming voice path:** mic → streaming STT → pipeline → streaming TTS, spoken progress, speech that pauses when the owner talks and is then dropped or resumed (Q47, Q50), and wired `stop`/`steer` frames chosen by Owl's understanding, not keywords (Q40). ★ Replacing GPL Piper stands alone [03 §7].
12. **Action metadata** (*derived from Q11, Q16, Q35, Q36 and Q49, not a report finding*): every action declares whether it is reversible and what its undo is, and scheduled work records which irreversible actions the owner explicitly set it up to take.

---

## 9. Spikes before committing

S1–S9 run on a GPU Linux box, an M-series Mac, a CPU-only laptop and a separate Jetson Orin, **not the dev box**, recording P50/P95 [03].

| Spike | Pass | Fail | Unlocks |
|---|---|---|---|
| **S1** Detected end of turn → non-spoken acknowledgement; end of speech → first audio; per tier, stub LLM then real pipeline | Acknowledgement ≤ 300 ms after end of turn is detected, on every tier; GPU/Mac P50 ≤ 800 ms, P95 ≤ 1.5 s; CPU/Jetson P50 ≤ 2.5 s | GPU/Mac P50 > 1.2 s, or acknowledgement over 300 ms | Pipecat adoption (Q28), tier bounds, Q39 |
| **S2** StackOwl turn: first answer token and first progress over 30 voice prompts | Chit-chat ≤ 700 ms; tool turns show progress ≤ 1 s | Chit-chat > 2 s | Whether the spoken "on it" needs its own path |
| **S3** STT on 100 owner phrases + 20 noise clips | WER ≤ 8% GPU, ≤ 12% CPU; no hallucinations | Over bound | STT per tier |
| **S4** TTS first audio + owner's blind ranking | ≤ 250 ms GPU / 600 ms Mac / 1 s CPU; a permissive engine in his top two | Neither met | Default voice set (Q30) |
| **S5** Barge-in and echo on iPhone, Android, laptop; WebRTC vs WebSocket | Speech pauses ≤ 300 ms (≥ 90%); after a backchannel or noise it resumes where it left off; ≤ 1 false barge-in / 10 min | More, or paused speech lost after a backchannel | Transport; hands-free viability; Q47 |
| **S6** End of turn on 50 hesitant utterances | Cut-offs ≤ 5%; detection delay ≤ 400 ms (the S1 acknowledgement clock starts after it) | Cut-offs > 10% | Turn detection |
| **S7** Installed PWA: permissions, lock, app switch, headset | 10 min, no re-prompt; announced pause/resume; after backgrounding, ambient cues fall silent and hands-free shows as paused; one tap resumes it and starts the briefing | Re-prompt each session | Phone comms posture; Q37, Q51 |
| **S8** Probe four hosts, then force-degrade | Right tier; honest fallback to push-to-talk | Wrong tier or silent failure | Q27 ladder |
| **S9** Speak over Owl during speech, generation and a running tool, with stop, steer, new-question, transcript-correction, backchannel and noise utterances | Speech pauses at once; Owl's understanding tells them apart with no word list; stop, steer, a new question and a transcript correction drop the paused speech, and after a correction Owl answers the corrected sentence fresh; a backchannel or noise resumes it where it left off; no orphan audio or duplicate task | Any misrouting, wrong drop or resume, an answer continued to misheard words, or divergence | Q24, Q40, Q47, Q50 semantics |
| **B1** Fresh clone, no domain, stock iPhone and Android: private network, StackOwl certificate-authority install, setup code (to the single allowed Telegram user; terminal only with none or several), open app, passkey, a second device approved from the first, mic, PWA install, push | Works at home and remotely with guided steps only; Telegram offers no device approval | The guided certificate install is unworkable on a stock iPhone (triggers the Q42 free-domain fallback), or a router change or third-party tunnel is needed | Q9, Q17, Q26, Q41, Q42, Q46, Q48 |
| **B2** Real event rates including a burst, streamed to a phone on mobile data with backgrounding | Staleness shown within heartbeat timeout; resume replays the gap without loss or duplicates; bounded memory on the Jetson | Silent gaps, or breathing continues after the stream dies | SSE vs WebSocket; retention |
| **B3** WebGPU and WebGL Viewscreen, 30 min of recorded events, mid-range Android and older iPhone | Holds its frame budget without throttling; drain within a budget set beforehand; low-power mode triggers | Throttles or over budget | Tier thresholds (Q22) |
| **B4** Map every event type to one visual; replay a real recorded day | Every mover traces to a record; quiet or failing reads as such; idle vs event motion told apart at a glance | A mover without a referent, or false activity | Viewscreen visual grammar |

The optional wake-word and full-duplex spikes (S10, S11) are dropped by Q7 and Q28.

---

## 10. The mockup phase (proposal for the next approval)

**Covers:** the Viewscreen and all six stations; phone and desktop postures, including the phone opening on the compact viewscreen and Needs-you strip and a notification opening straight on its item; every presence state including stale and the thinking acknowledgement; the low-power 2D tier; the accent against cream light, with a failure Owl could not heal beside one it healed; ambient cues beside the Needs-you alert; a Needs-you approval end to end, with read-back and tap; a crew request approved by a spoken yes after read-back; the owner's own spoken reversible order running at once with undo; an anticipation card with undo; the record of a scheduled irreversible run; a new-device approval on a signed-in device; the opening briefing on screen, then spoken on first tap, including on reopening a backgrounded phone; a long voice task with an interruption Owl reads as a steer, a backchannel after which Owl resumes, and a transcript correction after which Owl answers the corrected sentence fresh; a flight-recorder time-lapse.

**Form:** a clickable prototype driven by a recorded sample of real events from this box, plus scripted realistic events where no source exists yet. Voice uses pre-rendered clips from a bundled permissive voice. Every screen carries a visible "MOCKUP: sample data" label.

**Won't prove:** latency, battery or thermals (S1, B3); that the event stream exists (B2); consent and authority safety (§8); speech accuracy (S3); that Owl's understanding routes interruptions, including corrections, and drops or resumes its speech correctly (S9); HTTPS and remote access (B1). It is thrown away, not shipped.

---

## 11. Open engineering questions

1. **Pre-built assets** (Q43): how a fresh clone receives built front-end assets without Node, and what proves they match their source.
2. **Event transport:** SSE over HTTP/2 plus POST, WebSocket, or WebTransport; heartbeat, resume cursor, gap replay [01 §6.6].
3. **Where the web server lives:** durable gateway vs state-holding core ([02 gap 13] says placement needs a vote).
4. **Event schema:** types, ids, sampling, retention, relation to `audit_log` and jsonl.
5. **Core query path:** new IPC frame or separate socket.
6. **Certificates** (Q42): the lifecycle of StackOwl's own certificate authority (issuance, rotation, revocation, trust per device) for a LAN or WireGuard address; B1 decides whether the free-domain fallback is needed.
7. **WireGuard behind carrier-grade NAT** without router changes; whether WebRTC needs STUN/TURN inside the tunnel.
8. **Voice process** as a channel adapter in the one loop; authenticating an offloaded speech machine.
9. **Ignoring Owl's own voice and sounds,** so its speech does not pause itself (Q40 removes keyword detection; Q47 resumes speech only after a backchannel or noise; Q50 drops it after a correction).
10. **Silent mode and alerts:** whether a page can detect silent mode, which both Q51 sound classes and the Q39 acknowledgement sound depend on (Q37 settles the gesture rule for the briefing); whether a phone notification can carry the Needs-you alert.
11. **Mirrored approvals:** first-answer-wins between Telegram buttons and the strip; new-device approvals are never mirrored (Q48).
12. **Voice assignment** when owls outnumber bundled voices.
13. **Guard tests:** which of the 13 test-pinned invariants move over and which are deleted with the old app [02 §1.7]; "no framework, no CDN, no build step" is replaced by Q43.

---

## 12. Decision index

| Q | Decision | Answer |
|---|---|---|
| Q1 | Metaphor | Ship bridge: owner captain, owls crew, platform ship |
| Q2 | JARVIS essence | Talks and anticipates, acts correctly, aware of everything |
| Q3 | Users | One owner per install; teams later |
| Q4 | Surfaces | One mind, many surfaces; bridge alone has full control |
| Q5 | Control | Watch, steer, configure, internals; existing consent; no back door |
| Q6 | Motion | Truthful only; tap shows cause; heartbeat-driven breathing |
| Q7 | Voice start | Push-to-talk + hands-free; no wake word; limited, mutable proactive speech |
| Q8 | Postures | Phone = comms, desktop = full bridge (phone opening: Q38) |
| Q9 | Reach | From anywhere, self-hosted (one named exception: Q46) |
| Q10 | Host | Secretary is the ship's voice; owls speak when addressed |
| Q11 | Anticipation | Acts within authority, shows it, offers undo; asks otherwise (irreversible: Q36) |
| Q12 | Opening | Live viewscreen + short briefing; conversation one tap or word away (word: hands-free only, Q37) |
| Q13 | Identity | Logo-derived; cream light for normal activity; one accent only for needs-you (amended by Q34) |
| Q14 | Sound | Subtle, on, volume, respects silent mode; two classes (Q51): ambient cues that duck under Owl's voice, and the Needs-you alert |
| Q15 | Replay | Flight recorder from real events |
| Q16 | Voice authority | Request anything; irreversible needs read-back + tap (orders vs requests: Q49) |
| Q17 | Sign-in | Passkeys + one-time recovery code |
| Q18 | Host name | "Owl", renamable |
| Q19 | Liveness | Quiet but alive; dark cockpit |
| Q20 | Layout | Viewscreen + six stations + Needs-you strip |
| Q21 | Interrupts | One queue everywhere + phone notification; pause/steer/take over, never forced (device approvals never on Telegram: Q48) |
| Q22 | Rendering | WebGPU/WebGL Viewscreen only; automatic low-power 2D |
| Q23 | Presence | Owl mark alive through light and motion |
| Q24 | Long tasks | Ack < 1 s, milestones; interrupt pauses speech; "stop" stops task (refined by Q39, Q40, Q47, Q50) |
| Q25 | Licences | Permissive only; replace Piper; NVIDIA optional extra (refined by Q44) |
| Q26 | Access | Built-in WireGuard + automatic home HTTPS; others optional |
| Q27 | Hardware | Probe; push-to-talk everywhere; honest hands-free; offloadable |
| Q28 | Voice stack | Pipecat if spikes pass; cascade; no speech-to-speech |
| Q29 | Current login | Close the admin/admin exposure now (separate fix; the approach was replaced by L1–L4: setup code, no default password) |
| Q30 | Owl voices | Distinct bundled voice each; Owl's most distinctive |
| Q31 | Cloning | None |
| Q32 | Transcripts | Auto-send, live; "no, I said…" corrects (refined by Q35, Q40, Q50) |
| Q33 | Repository housekeeping | Research reports committed; not a product decision |
| Q34 | Accent colour | Normal activity in cream light only; accent only when the owner is needed; unhealed failure = higher-intensity Needs-you item, healed failure only in its record |
| Q35 | Misheard voice | Transcripts keep auto-sending; the owner's own voice order triggers at most a reversible action, at once, with undo, no read-back (Q49); irreversible needs read-back + tap |
| Q36 | Irreversible autonomy | Only when the owner explicitly set it up beforehand, each run a tappable record; otherwise Needs-you; a spoken yes to an Owl or crew request counts only after read-back (Q49) |
| Q37 | "One word away" | Hands-free only; briefing on screen first, spoken after first tap or at once if hands-free is on; not proactive speech; a backgrounded phone pauses hands-free, and the one tap on reopening also starts the briefing |
| Q38 | Phone opening | Compact viewscreen + Needs-you strip, Comms one swipe away; a notification opens its item |
| Q39 | Slow hardware | Non-spoken ack within ~300 ms of detected end of turn on every tier (thinking mark + soft sound, which is feedback, in neither Q51 sound class); spoken "on it" as fast as possible, < 1 s on GPU/Mac |
| Q40 | Stop / steer / correct | No keyword matching; speaking pauses Owl; Owl's normal understanding decides (paused speech: Q47, Q50) |
| Q41 | First passkey, new devices | Setup code in terminal + owner's Telegram (the single allowed Telegram user; terminal only if none or several) registers the first passkey; new device approved from a signed-in dashboard device (Needs-you) or with the recovery code (never Telegram: Q48) |
| Q42 | HTTPS fallback | Guided install of StackOwl's own certificate authority first; guided free domain if B1 fails on stock iPhones; never a required third-party tunnel; the fallback is principle 7's one named exception (Q46) |
| Q43 | Front-end tooling | Build step and libraries allowed; every asset vendored and platform-served; fresh clone gets pre-built assets, no Node |
| Q44 | NVIDIA-licensed models | Never bundled; permissive-only default install; owner may download on NVIDIA hardware, licence shown first |
| Q45 | Current dashboard | Deleted in the change that ships the bridge, not before; keeps the Q29 login fix until then |
| Q46 | Fallback domain vs self-hosted | No feature requires a third-party service; the Q42 free-domain fallback (public DNS + public certificate service, only if B1 fails on stock iPhones) is the one named exception |
| Q47 | Owl's speech when the owner talks | Pauses at once; a steer, stop, new question or transcript correction drops it (correction: Q50); a backchannel or noise resumes it where it left off |
| Q48 | New-device approval | Only from an already signed-in dashboard device or with the recovery code; never from Telegram, though other approvals are mirrored there |
| Q49 | Voice orders vs voice approvals | Owner's own order for a reversible action runs at once, with undo, no read-back; a request from Owl or crew needs read-back before a spoken yes counts; irreversible needs read-back + tap |
| Q50 | Paused speech after a correction | Drops the paused speech, applies the correction, answers the corrected sentence fresh; never continues an answer to words the owner did not say |
| Q51 | Two classes of sound | Ambient cues: very soft texture for ordinary events; never repeat, duck under Owl's voice, silent when the phone is backgrounded, switchable off on their own. Needs-you alert: the only attention-grabbing sound, the accent colour's sound twin. Ambient cues never compete with it (principle 2); the Q39 acknowledgement sound is feedback, in neither class |
| Q52 | Misheard order that already ran | A correction ("no, I said…") automatically undoes the reversible action the misheard sentence triggered, shows it on the card, then does what the owner actually said |

**Current-dashboard login (L1–L4)** (approved; a separate fix in progress that implements Q29):
- **L1:** a fresh install proves ownership with a one-time setup code, printed in the platform's terminal and sent to the owner's Telegram (the single allowed Telegram user; with none or several, the terminal/CLI only), plus a new password. No `admin/admin`; no token is issued for a publicly known password. The bridge reuses this setup code for its first passkey (Q41).
- **L2:** the password is removed from `stackowl.yaml`, set only on the dashboard and stored once as a salted hash. An existing custom YAML password is imported once on upgrade.
- **L3:** an unreadable password store is never treated as "no password". The lookup is retried on each sign-in, sign-in is refused with a remedy, and the failure is reported to self-healing as an incident. A damaged record returns the install to setup-code mode.
- **L4:** `stackowl control-plane reset-password` on the host clears the password and issues a fresh setup code, without a restart.
