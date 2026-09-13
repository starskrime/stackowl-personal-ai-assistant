# Helm Dial v2 — brief (design lead)

**Owner verdict on mockup v1 (2026-09-12):** "Like Helm Dial but elements not clear. Agents should be there and what they are doing, memories, jobs, etc. It should show real animation of what the agents and the platform do."

**Question v2 answers:** can the owner read, at a glance and without a legend lookup, WHO is doing WHAT on his platform right now — crew, jobs, memory, comms, engineering — from real replayed activity?

Everything in `BRIEF.md` still binds (tokens, type, truthful motion, amber = needs-you only, engine contract §4 and its documented additions in `engine.js` header, DOM twins, reduced motion, low power, stale). This brief replaces §5-A only. Build it as a NEW file `variant-a2.js` registering `window.BridgeVariants.A2 = {key:'A2', name:'Helm Dial v2', mount, unmount}`; keep `variant-a.js` untouched for comparison. The shell switcher cycles only `A2` (default) and `A` (v1) from now on; B and C stay in the files but leave the menu.

Real data reality (from `data.js`, do not invent): 72 h window, 3,997 events — heal 934, job_run 554, turn 443, task 384, tool_call 379, model_call 357, incident 202, memory_write 177, consent 160, health_change 143, budget_alert 138, delivery 101, delegation 25. Most activity is `secretary` (host, displayed by its owl name), `scheduler`, `loop`; several owls are idle for the whole window — show that truthfully, never fake activity. 33 live recurring jobs (136 finished one-shots are not drawn).

## 1. Anatomy — the ship as concentric layers, every layer LABELLED on screen

Desktop layout: three columns — **Crew** panel (left, 280 px) · **Dial** (centre, square, as large as fits) · **Ship's log** (right, 340 px). Needs-you strip and captions stay where the shell puts them.

Inside the dial, from outside in (each ring carries a small Big Shoulders uppercase label on its own arc, e.g. `SCHEDULE`, `COMMS`, `CREW`, `MEMORY`, `ENGINEERING` — so no legend is needed):

1. **SCHEDULE — outer bezel.** 24-h clock (local time, Martian Mono hour numerals), the replay "now" hand. Every live recurring job is a tick at each due time in the next 24 h. The **next three due jobs** are written as short labels beside the hand (`telegram_canary · 15:00`). When a `job_run` event arrives, its tick flares and a thin light travels inward along a radius to the actor that handled it (usually `scheduler` in the engineering band); failed = the light is dashed and stops short.
2. **COMMS — docking ports on the rim.** One labelled port per channel present in the data (`telegram`, `cli`, `slack`, …) at fixed rim angles. A `turn` event = a packet travelling port → host (centre); the answer's `delivery` = packet host → port; failed delivery = packet returns dashed and a small hollow mark stays on the port with a count.
3. **CREW — the crew ring.** Every owl in `snapshot.owls` is a small **module**: a mini stacked-block glyph derived from the logo mark (three bars; NOT the full mark, which is reserved for the host) + its display name in Atkinson 13 + a one-line **doing** text in Martian Mono 11 under it (`thinking…`, `tool: process`, `task claimed`, `resting · last active Wed 21:09`). Module states, all in cream by form: resting (dim, hollow bars), thinking (bars light in sequence on `model_call`), using a tool (top bar solid + spoke, see §2), waiting for consent (small lock glyph), needs-you (amber ring — only if a needs-you item names this owl). `scheduler`, `loop` and other non-owl actors are NOT crew — they live in ENGINEERING.
4. **MEMORY — an inner ring around the centre**, split into three labelled arcs by what the data's `memory_write` detail/kind says (e.g. lessons · reflections · facts; derive the real kinds, fall back to one arc if unknown), each with a live count. A `memory_write` = a light particle flowing from the writing module into its arc; the arc segment brightens for 1.5 s; the count ticks.
5. **ENGINEERING — the lower 120° band between crew and memory**, labelled segments for the subsystems that actually appear as actors/targets in the data (`scheduler`, `loop`, `db`, `provider` or model, `health`, `self-heal`, …; take them from events, cap ~8, group the rest as `other`). `heal` = the segment pulses cream once and a tiny `healed` tick stays for 30 s; `incident` = a hollow outline on the segment, amber only when a needs-you item exists for it; `health_change` = segment solid (ok) vs hollow/dashed (degraded); `budget_alert` = the `fuel` segment's gauge ticks.
6. **HOST — the centre.** The host owl (`secretary`, shown by its name) as the full logo mark, animated by `Bridge.presence` and by its own events (thinking on its `model_call`, speaking during captions). Under it: its name and its own doing-line.

## 2. Motion grammar — one distinct, learnable motion per event type (all ≤ 900 ms travel, then a 2 s fade trail)

| Event | Motion |
|---|---|
| `turn` | packet: channel port → host |
| `delivery` | packet: host/owl → channel port (failed: bounce back dashed) |
| `model_call` | the actor's module/host bars light in sequence (no travel) |
| `tool_call` | a short straight **spoke** from the module outward, the tool name appears at its tip for 1.5 s, returns (failed: dashed, no return) |
| `delegation` | a curved arc between two crew modules (or host → module) |
| `task` | a small square token travels along the crew ring from `loop` (engineering) to the owl that claimed it; `dead-lettered` → token drops into a labelled `DEAD LETTER` tray at the band's edge with a count |
| `job_run` | bezel tick flare + inward radius light to the handler |
| `memory_write` | particle: module → its memory arc |
| `heal` / `incident` / `health_change` / `budget_alert` | engineering band as §1.5 |
| `consent` | lock glyph on the module: opens (granted) / stays shut 3 s (denied) |

Replay default speed **10×** (set it at mount if the engine is faster; restore on unmount). At 60× cap concurrent travellers (~24) and aggregate the rest into a `+N` pulse on the target, so the dial never becomes noise.

## 3. Side panels — plain language, live

- **Crew panel (left):** one row per owl (host first): module glyph · name · doing-line · time since last event (Martian Mono, tabular). Active rows sit at the top and briefly highlight on each event; idle rows stay truthful (`resting · last active …`, or `no activity in this recording`). Clicking a row opens the Crew station for that owl.
- **Ship's log (right):** newest first, one line per replayed event in plain English, written from the owner's side: `14:03  Friday → used tool process ✓`, `14:03  scheduler ran telegram_canary ✓`, `14:04  healed: database reconnected`, `14:05  jobmarket finished its turn ✓`, `14:06  memory: 1 lesson saved by Friday`. Failures read as failures (`✕ delivery to telegram failed`). Group bursts of identical events (`scheduler ran health_sweep ×6`). Each line is a button that opens its record. Cap 200 lines.

## 4. Phone (≤ 700 px)

Dial on top (square, full width; ring labels shortened; next-due labels hidden), then a segmented control `Now | Log`: **Now** = the crew panel rows; **Log** = the ship's log. Needs-you strip stays under the header as the shell does it.

## 5. Quality

Labels never overlap: place ring labels and module labels by computed angle with collision nudging; owl modules evenly spaced on their ring. Every module, port, segment, tick and log line has a DOM twin / is a real button. Low power: no travel animation — targets update state and the log still streams. Stale: all travel freezes, dial dims to `--stale`, log header shows `last event … ago`. ≤ 30 fps. Unmount removes everything. `node --check` must pass.
