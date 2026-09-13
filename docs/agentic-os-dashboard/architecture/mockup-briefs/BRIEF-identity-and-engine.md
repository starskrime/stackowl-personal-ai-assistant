# StackOwl Bridge — MOCKUP brief (design lead)

Throwaway prototype answering ONE question for the owner (Boss): **"What should the Bridge look and feel like?"**
Source of truth for WHAT it must honour: `/ssd/projects/stackowl-personal-ai-assistant/docs/agentic-os-dashboard/full-picture.md` (approved; Q1–Q52). Read §1, §2, §3, §4, §6 and §10 before building. This brief decides HOW it looks.

One line (prototype skill): **Three structurally different Bridge variants in one published page, switchable via `?variant=A|B|C`, replaying a recorded sample of real events from the owner's box, labelled MOCKUP.**

---

## 1. Delivery shape (fixed)

Multi-file static Artifact, no build step:

```
bridge-mockup/
  index.html        shell: tokens, fonts, MOCKUP label, switcher, posture + power toggles,
                    Needs-you strip, Owl presence mark, captions, station sheet frame,
                    loads engine.js, data.js, variant-*.js
  engine.js         window.Bridge — replay engine + scenario script (shared, no layout)
  data.js           window.BRIDGE_DATA = <recorded-events.json contents>
  variant-a.js      window.BridgeVariants.A
  variant-b.js      window.BridgeVariants.B
  variant-c.js      window.BridgeVariants.C
```

Artifact CSP: scripts only from `https://cdnjs.cloudflare.com` (pin exact versions, e.g. three.js r128 `https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js` if a variant truly needs WebGL — Canvas 2D is preferred unless 3D carries the idea); stylesheets only from Google Fonts; NO fetch/XHR, NO images from other hosts, NO localStorage dependence (wrap any use in try/catch). Relative paths without a leading slash.

The page must open in a realistic working state (replay already running at 60×, a Needs-you item present), never an empty shell.

## 2. Identity tokens (single dark theme — a deliberate commitment: the Bridge is a dark-cockpit instrument; paint `body` and every colour explicitly)

| Token | Hex | Role |
|---|---|---|
| `--hull` | `#0b0e12` | ground (the logo's tile) |
| `--deck` | `#11161c` | raised panels, sheets (blue-biased neutral, never grey) |
| `--rule` | `#1f2730` | hairlines, grid, bezel ticks |
| `--cream` | `#f4f1ea` | instrument light: primary text, ordinary activity motion (the logo's mark) |
| `--cream-dim` | `#8d897f` | secondary text, idle marks, recorded-past |
| `--caution` | `#ffb020` | THE accent — master-caution amber. ONLY for "needs you" (Q34). Unhealed failure = same amber at higher intensity (solid, slow pulse); nothing else may use it |
| `--caution-ink` | `#1a1204` | text on amber |
| `--stale` | `#5b6470` | a dead/stale stream: breathing stops, marks desaturate to this |

Rules: ordinary activity (turns, tool calls, job runs ok, deliveries ok) moves in `--cream` only. Healed failures show NO colour — a small cream "healed" glyph in their record. No green/red status colours anywhere; state is carried by form (solid / hollow / dashed / struck) plus the single amber.

Type (Google Fonts, real fallbacks):
- Display — **Big Shoulders Display** 600–800, uppercase, letter-spacing .08em, for station names and the few big readouts. Echoes the logo's stacked blocks and bulkhead signage. Use sparingly.
- Body — **Atkinson Hyperlegible Next** 400/600 (fallback: `"Atkinson Hyperlegible", system-ui, sans-serif`) — cockpit legibility, low-vision friendly.
- Data — **Martian Mono** 400/500 (fallback `ui-monospace, "SFMono-Regular", monospace`), `font-variant-numeric: tabular-nums`, for timestamps, counts, cron schedules, durations.
Scale (px): 11 / 13 / 15 / 18 / 24 / 36 / 56. Captions 15 body. Labels 11 display-uppercase.

Motion language (Principle 1 truthful motion): every moving mark corresponds to a replayed event and opens its record on tap/click. Idle breathing = the engine heartbeat (period 4 s), never an unrelated CSS loop. Event transitions are short (≤ 600 ms). `prefers-reduced-motion` or the LOW-POWER toggle → no continuous motion, events appear as static state changes. Stale mode (toggle) → breathing stops, a "last event 00:42 ago" readout ticks up, marks drop to `--stale`.

Owl presence = the logo mark itself, geometry untouched (path below), animated only by light: idle (slow cream breathe on heartbeat) · listening (eyes brighten with a simulated mic level) · thinking (the three blocks light in sequence) · speaking (blocks modulate with a caption-driven envelope) · needs-you (amber) · stale (static `--stale`).

```
M3.5 2H20.5A1.5 1.5 0 0 1 22 3.5V7.5A1.5 1.5 0 0 1 20.5 9H13.4L12 6.9L10.6 9H3.5A1.5 1.5 0 0 1 2 7.5V3.5A1.5 1.5 0 0 1 3.5 2Z M9 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z M15 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z M3.5 10.5H20.5A1.5 1.5 0 0 1 22 12V14A1.5 1.5 0 0 1 20.5 15.5H3.5A1.5 1.5 0 0 1 2 14V12A1.5 1.5 0 0 1 3.5 10.5Z M6.5 17H17.5A1.5 1.5 0 0 1 19 18.5V20.5A1.5 1.5 0 0 1 17.5 22H6.5A1.5 1.5 0 0 1 5 20.5V18.5A1.5 1.5 0 0 1 6.5 17Z
```
(viewBox 0 0 24 24, `fill-rule="evenodd"`). For per-block lighting, draw the three blocks as separate subpaths of the SAME coordinates.

Copy: from the owner's side of the screen; real platform terms where they are the owner's vocabulary (owl names, job names, cron schedules, "dead-lettered", provider tiers). No lorem. Every scripted (not recorded) moment is marked `SCRIPTED` in its card.

## 3. Shell (index.html) — shared by all variants

- Fixed top-left: `MOCKUP · recorded sample from this box · <window>` in Martian Mono 11, cream-dim, plus Owl presence mark (28 px) and the host name "Owl".
- **Needs-you strip**: top edge, full width on desktop, sticky under the header on phone. Holds engine `needsYou` items as amber chips; empty = a single hairline (dark cockpit). Tap → opens the item's card in the station sheet with its action (approval: read-back text + "Confirm" button; unhealed failure: what failed, what Owl tried, "Resume job" / "Keep paused").
- **Captions bar**: bottom, above the switcher — Owl's speech as live captions with the owner's own utterances in cream-dim italics; mic-live indicator dot (cream) when "listening".
- **Station sheet**: a frame any variant opens (`Bridge.openStation(name, recordId?)`): right-side sheet on desktop (420 px), bottom sheet on phone (88 vh). Six stations: Comms, Crew, Missions, Engineering, Archives, Security. Content comes from the snapshot + replayed events; each station lists what [02] says it will be fed by, and marks what is not available today as `NOT STREAMED TODAY` (truthful).
- **Floating switcher** (prototype skill spec): bottom-centre pill, visually NOT part of the design (white `#ffffff` pill, black text, system-ui 13 px, shadow) — `‹  B — Flight Deck  ›`, ← / → keys (not while an input is focused), updates `?variant=` with `history.replaceState`. Beside it, two small toggles in the same pill: `Phone | Desktop` posture (forces a 390×844 frame centred on desktop screens; auto-phone under 700 px wide) and `Full | Low power`, plus `Stale` and a scenario menu: `Briefing`, `Approval`, `Undo card`, `Voice task + interrupt`, `Flight recorder`, and replay speed `1× 10× 60×`.

## 4. Engine contract (engine.js) — the ONLY API variants may use

```js
window.Bridge = {
  data,                         // BRIDGE_DATA (snapshot + events)
  now(),                        // replay clock (Date) — starts at window.from, loops at window.to
  speed, setSpeed(n),           // 1 | 10 | 60
  posture,                      // 'phone' | 'desktop'
  power,                        // 'full' | 'low'   (low also when prefers-reduced-motion)
  stale,                        // boolean
  heartbeat,                    // 0..1 phase of the 4 s breathe (frozen when stale)
  presence,                     // 'idle'|'listening'|'thinking'|'speaking'|'needs-you'|'stale'
  needsYou,                     // [{id, kind:'approval'|'unhealed'|'device', title, detail, severity:1|2, scripted:boolean, recordId}]
  on(evt, fn), off(evt, fn),    // evt: 'event' (a replayed recorded event), 'tick' (every frame, {dt}),
                                //      'needsYou', 'presence', 'caption' ({who:'owl'|'owner', text, final}),
                                //      'posture', 'power', 'stale', 'scenario' ({name, step}), 'reset'
  recent(ms),                   // recorded events within the last ms of replay time
  openStation(name, recordId),  // shell sheet
  runScenario(name),            // 'briefing'|'approval'|'undo'|'voice-interrupt'|'flight-recorder'
  seek(date), flightRecorder(on) // time-lapse: replays the whole window in ~30 s, then returns to live
};
window.BridgeVariants = window.BridgeVariants || {};
// each variant: { key:'A', name:'…', mount(rootEl, Bridge), unmount() } — must remove its listeners, rAF and canvases on unmount
```

Scenario scripts (SCRIPTED, derived from §1 of the full picture, using REAL owl/job names from the snapshot):
- **briefing**: presence thinking→speaking; captions summarise the replay window with real counts (runs, failures, heals, delegations, dead-lettered); each named item emits `{name:'briefing', step, recordId}` so variants light it.
- **approval**: a crew owl asks to send something to a channel → Needs-you approval → owner says "Post it." → Owl reads back channel/text → Confirm → item leaves strip.
- **undo**: owner order "Start the comparison" runs at once → card with Undo (Q35/Q49).
- **voice-interrupt**: long task, milestone speech, owner cuts in "Include the older model." → speech pauses → steer → dropped remainder → task continues (Q47); then "no, I said…" correction path auto-undoes (Q50/Q52).
- **flight-recorder**: 30 s time-lapse of the whole window.
Initial state: replay at 60×, one real unhealed failure (pick a real failed job from the data if any, else scripted) + one scripted approval in Needs-you.

## 5. The three variants — structurally different (different primary object, hierarchy and navigation)

**A — "Helm Dial"** (station ring). Desktop: a large circular Viewscreen centred. The outer bezel is a 24-hour dial (the ship's clock) with cron ticks for every enabled job at its next due time — silent jobs drawn hollow. Inside, owls sit as crew positions around an inner ring at fixed stations; each replayed event is a short cream arc of light from its actor to its target (channel/tool/job) along the ring, fading over 2 s. The centre holds the Owl mark and the Needs-you count. The six stations are named arcs of the outer rim you click to open the sheet. Phone: the dial shrinks to the top third (bezel only + crew ring), the strip under it, Comms is a swipe-left panel with the conversation.
Primary affordance: the rim (stations) and the clock (time).

**B — "Flight Deck"** (wake + instruments). Desktop: no circle. A wide horizontal **wake** across the upper half — time flows right→left like a sonar/ship's-wake trace; each owl is a lane, events are marks on its lane (tick = tool call, bar = job run, hollow = failed-then-healed, amber diamond = needs you); "now" is a bright vertical line at the right third, and the space right of it shows PROJECTED marks for jobs due soon (dim cream). Below the wake: a row of large instrument readouts in Big Shoulders — HULL (health subsystems ok/total), FUEL (cost last 24 h), COMMS (deliveries ok/failed, undelivered outbox), CREW ACTIVE, DEAD-LETTERED — each a tap target into its station. Stations are console tabs along the bottom. Phone: the wake rotates to vertical (time flows down), instruments become a 2-column grid under it, tabs become a bottom bar.
Primary affordance: the timeline lanes and the instrument readouts.

**C — "Night Chart"** (sky with sheets, minimal chrome). Desktop: full-bleed Canvas sky. Owls are named stars placed by activity (brighter = more events in the last hour, dim = idle); channels, tools and jobs are faint fixed points at the edges; each replayed event launches a short comet along a curved path from actor to target and leaves a fading trail; older activity recedes (smaller, dimmer) — depth is time. No panels on screen: stations are six edge labels (top/right/bottom/left) that pull a sheet over the sky. Phone: the sky is the background behind a sheet-first layout; the strip and captions sit over it; tapping a star opens Crew.
Primary affordance: direct manipulation of the sky (tap a star/comet) and edge-pull sheets.

Each variant must visibly show: truthful motion from replayed events; idle breathing tied to `heartbeat`; the stale state; the low-power state (static 2D); phone and desktop postures; the Needs-you strip interplay; tapping any mark opens its record.
Shared visual components beyond the shell are NOT allowed — each variant owns its own layout.

## 6. Quality bar

Keyboard focus visible; every Canvas-drawn entity has a DOM twin (a visually-hidden list of the current marks with buttons) — Principle 5. No horizontal page scroll. No clipped text. Performance: requestAnimationFrame only while visible (`document.hidden` pauses), ≤ 30 fps ambient in full power, cap events drawn per frame; must stay smooth on a mid-range phone.
