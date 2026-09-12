# 02 — Platform surface inventory for an agentic-OS dashboard

**Date:** 2026-09-12. **Scope:** read-only investigation of `main` @ `34e022da`. No source was modified.
**Evidence levels:**
- **[live]** means measured against the running instance on this box (core pid 2155372, `0.0.0.0:8787`).
- **[code]** means read from the source at the cited `file:line`.
- **[sweep]** means reported by a code sweep and not re-verified line by line.

Where two sweeps disagreed, the claim was checked and the wrong one is corrected in place.

---

## Executive summary

1. **Today's dashboard is a read-only JSON API plus one static page, and it runs in the core process.**
   - `control_plane/server.py` is an aiohttp `SupervisedTask` with eight authenticated `GET /api/v1/*` routes, one `POST /api/v1/login`, and unguarded `/`, `/manifest.webmanifest` and `/icon.svg` (`server.py:167-179`).
   - It is registered only when `role != "gateway"` (`startup/orchestrator.py:4563-4577`). In the split deployment it dies and rebinds with every core `os.execv` restart.
   - It has **no write route, no push stream, and no polling**. The page fetches each panel once, after sign-in (`page.py:834-859`), and contains no `setInterval`, `setTimeout`, `EventSource`, `WebSocket` or service worker [code].
2. **The data it shows is real, not mocked.** Every route reads the live scheduler, the live `DbPool`, the live `HealthAggregator` or the config file. There is no placeholder data. Live payloads [live]:
   - 15/15 health subsystems ok (a snapshot that took 0.63 s)
   - 11 owls, 7 of them unbounded
   - 5 live tasks, with 77 dead-lettered not shown
   - 170 jobs, 3,174 runs in 24 h, and **133 enabled jobs silent** (*corrected 2026-09-12:* 136 of the 169 enabled jobs are one-shot `rollover_summary` jobs that completed but were never switched off (status `completed`, `enabled=1`, next run stuck in the past) and only the 33 others are live schedules — verified against the `jobs` table 2026-09-12)
   - 5,968 lessons
   - 69 interaction edges, with the newest delegation on 2026-08-24
   - 45 settings, 7 of them masked
3. **Three rendering defects in the page were verified against live payloads.** A guard test meant to catch the first one cannot see it. See §1.6.
4. **Nothing reaches a browser live.** The EventBus runs inside one process and saves nothing.
   - `ProgressEventFrame` is defined but **never constructed**, so no bus event crosses core→gateway (`ipc/frames.py:189`, zero call sites) [code].
   - The only SSE endpoint is the MCP tool transport, not an event feed.
   - Every "action visualised live" requirement needs a new event spine.
5. **There is no control surface a web caller could use safely.**
   - `CommandRegistry.dispatch` performs **no authorisation** (`commands/registry.py:60-96`) [code]. 33 commands are live, including `/bye` (shutdown), `/config`, `/provider` and `/owl` [live log].
   - Consent routing **auto-grants** ordinary consequential actions for any channel with no registered prompter (`tools/consent.py:664-685`) [code]. A new `web` channel would therefore be auto-granted.
   - In split mode `ConsentRequestFrame` carries no `reply_target` (`ipc/frames.py:225-242`) [code].
6. **There is one identity and one shared login.**
   - Every web caller is `principal-default` with all three severities (`control_plane/auth.py:265-269`).
   - The login is admin/admin by default, and the live boot log confirms it is still the default and `reachable_off_this_machine: true` [live].
   - There is no users table, password hashing or session store.
7. **Voice input exists and runs locally; spoken output does not.**
   - Speech-to-text is local `openai-whisper` (`base`) for Telegram voice notes and TUI push-to-talk. It is enabled by default and live on this box [live].
   - Text-to-speech exists only as an agent **tool** that writes a file (local `piper`, cloud opt-in). No channel sends audio as a voice reply.
8. **Deployment is plain HTTP on a LAN, on a Jetson** (aarch64, 6 cores, 7.4 GiB RAM, 3.6 GiB available at check time).
   - There is no TLS, reverse proxy or tunnel.
   - Browsers only grant microphone access (`getUserMedia`) and service workers on a **secure context** (HTTPS or localhost). The planned phone voice mode and an installable offline PWA are therefore blocked on transport security, not on UI work.

---

## Component inventory

**Column key:**
- **Live-readable?** asks whether a web server could read the component from outside core memory (e.g. SQLite in WAL mode, which permits concurrent readers; `db/pool.py:55-56`).
- **Dashboard today** asks whether `control_plane` already shows it.
- The live database is `~/.stackowl/workspace/stackowl.db` (`paths.py:44`). The file `~/.stackowl/stackowl.db` is a 0-byte stray.

| Component | Where | State source | Live-readable? | Actions available today | Gaps |
|---|---|---|---|---|---|
| **Owls / agents** | `owls/registry.py:179` (in-memory `OwlRegistry`), `owls/store.py:51` (`OwlStore`), `owls/manifest.py`, `tools/meta/owl_build.py` | `owls` table (0118, 11 rows), `owl_dna`, `owl_dna_authored`, `dna_checkpoints`, `skill_ownership`; curated `~/.stackowl/memory/<owl>.md`. The registry is rebuilt in memory at boot (`orchestrator.py:1103,1108`). | DB yes. The in-memory manifest can drift mid-session. **Dashboard today: `/api/v1/agents`** (card, bounds, ceiling, `unbounded`, in-flight, recent turns). | Tool `owl_build` create/edit/retire/rename/pause/resume/grant; `grant` is always-ask consent (`owl_build.py:100-108`) [sweep]. `/owl` subcommands (`commands/owls_command.py:63-172`). | No HTTP action. Persona lives inside `manifest_json` with no dedicated field. `owl_profiles` has 0 rows. Card and table disagree on skill counts (e.g. secretary 71 vs 9, `server.py:974-982`). |
| **Skills** | `skills/store.py:308`, `skills/loader.py`, `owls/skill_ownership.py` | Files under `~/.stackowl/skills/{builtin,installed,user,learned}` are the source of truth. The `skills` table (48 rows) is a cache, alongside `skill_audit` and `skill_ownership` (37). | Yes. **Dashboard today: ownership only** (`/api/v1/skills`, 7 owls). | `/skill` list/show/add/rm/edit/enable/disable/reload/pin; tools `skill_manage`, `skills_list`, `skill_view`, `synthesize_skills` [sweep]. | No catalogue route: the store has no list-all (`server.py:615-619`). No enable/disable over HTTP. |
| **Tools** | `tools/registry.py:312` (process singleton), `tools/_infra/discovery.py`, `tools/_infra/presentation.py` | **In memory only.** Discovered classes plus MCP tools plus learned specs (`~/.stackowl/workspace/tools/learned`). Per-turn outcomes go to `task_outcomes` and `tool_heuristics`. 79 tools were presented to unbounded owls (`server.py:1032-1037`). | **No** (catalogue); outcomes only. Dashboard today: no. | Presentation filtered by `bounds.permits_tool`; `owl_build grant`; `tool_build`; `/tools` (live-registered). | Catalogue needs a core query path. No per-tool usage or latency surface. |
| **Task loop / tasks** | `pipeline/durable/loop.py:85` (`TaskLoop`: 5 s tick, parallel workers, 900 s lease), `pipeline/durable/store.py:253`, wired at `orchestrator.py:1940-1950` | `tasks` (1,221 rows: pending 5, failed 849, dead_letter 77, completed 290 [sweep]), `side_effect_ledger` (1,974), `task_outcomes` (20,719). Lease/worker identity is held in memory. | DB yes. **Dashboard today: `/api/v1/tasks`** (unfinished only, with a `blocked` reason and a dead-letter count). | Store enqueue/claim/fail_and_requeue/mark_delivered; tools `task_status`, `delegate_task`, `sessions_spawn`, `objective`. | **No cancel/retry/requeue from any command or route.** Worker occupancy is not observable. No history view, since terminal rows are filtered out. |
| **Objectives** | `objectives/driver.py` (scheduler handler, `scheduler/assembly.py:388-398`) | `objectives` (0 rows), `objective_subgoals` (28), `objective_events` (49) [sweep] | Yes. Dashboard today: no. | `objective` tool; `/owl objective-cancel` / `objective-merge`. | Child rows without parents suggest pruning or orphaning. Not investigated further. |
| **Scheduler** | `scheduler/scheduler.py:177` (`JobScheduler`, `SupervisedTask`), `scheduler/run_history.py`, 34 handlers, `tools/scheduling/cronjob.py` | `jobs` (170), `job_runs` (~23k), `job_results` (~2.4k) | Yes. **Dashboard today: `/api/v1/schedules`** (jobs joined to 24 h run history in Python, `server.py:505-582`). | `pause`/`resume`/`snooze`/`stop_job`/`create_job`/`update_job`/`run_now` (`scheduler.py:1105-1348`) [sweep]; tool `cronjob` with 8 verbs. | No HTTP control. `jobs` has no `owner_id` (`server.py:479-481`). `circuit_broken_at` is vestigial. |
| **Memory (SQLite)** | `learning/lessons_store.py`, `memory/sqlite_bridge.py`, `memory/activity.py` | `lessons` (5,968), `reflections` (6,614), `staged_facts` (239, short-term history), `committed_facts` (0, retired), `learning_artifacts` (709), `user_preferences` (8) | Yes. **Dashboard today: `/api/v1/memory`** (50 newest lessons, counts by source, other stores as counts). | `/memory` stats/search/remember/forget/export; tools `memory`, `session_search`, `reflect_now`. | No search, pagination beyond 50, or forget over HTTP. |
| **Memory (curated md)** | `memory/curated.py:40-44` | `~/.stackowl/memory/USER.md` plus per-owl `<owl>.md` (19 files [live]) | Yes (files). Dashboard today: yes, but the entries **render as "[object Object]"** (§1.6). | `memory` tool add/remove. | Page defect; no edit surface. |
| **Memory (graph)** | `memory/kuzu_adapter.py`; opened only when role != gateway (`orchestrator.py:1132`) [sweep] | `~/.stackowl/kuzu/graph.kuzu`. LanceDB is **gone**: no import of it remains in `src/` [code]. | Core only; assume single-writer. Dashboard today: no. | Internal. | A graph view needs a core query path. |
| **Sessions / conversations** | `sessions/store.py:86`, `memory/transcript_store.py`, `pipeline/turn_persist.py` | `sessions` (147), `conversations` (1,455), `messages` (4,601) + `messages_fts`, `session_prompts`, `message_ledger` (561) | Yes. Dashboard today: **no**. | `/new`, `/reset`, `session_search`. | No conversation view or transcript browser: the thing a "talk to the platform" app would open first. |
| **Channels** | `channels/registry.py:16`; adapters `telegram/`, `slack/`, `discord/`, `whatsapp/`, `cli_adapter.py` (TUI), `socket_adapter.py`, `dev_ingress.py` | Adapter objects in the gateway process's memory. `channel_liveness` (2), `delivery_attempts` (6,134), `undelivered_outbox` (221), `notification_log` (7,625), `callback_log`. Live config sets only `telegram_channel` [live]. | Liveness and delivery rows yes; adapters no. Dashboard today: only indirectly (`telegram_receive`/`telegram_canary_send` health rows). | Start from config; `/webhook`, `/quiet`, `/notifications`, `/urgent`. | **No `web` channel exists.** Delivery failures and outbox are invisible to the dashboard. |
| **Providers / models / tiers** | `providers/registry.py:93` (`get_by_tier`, `get_with_cascade`, `describe_tier_ladder`), `config/provider.py` (tiers fast/standard/powerful/local), `providers/model_window.py` | `stackowl.yaml` `providers:`. Resolved context windows and circuit breakers are in memory. `cost_records` per call. | YAML yes; breaker and window state no. Dashboard today: provider health row plus masked config keys. | `/provider`, `/tier`; hot reload on `settings_reloaded`. | No per-provider latency, error rate or breaker view. No route showing which model a turn actually used. |
| **Consent / authz / grants** | `tools/consent.py` (`ConsentScope`:351, `ConsentRequest`:370-398, `RoutingPrompter`:641-686), `runtime/socket_consent.py`, `authz/bounds.py`, `authz/bounds_guard.py`, `authz/enforcement.py` | **Grants in memory only, by design** (`consent.py:728-744`: restarts must not resurrect a grant). Durable trace is `audit_log` `consent.decision`. Bounds and ceilings live on the owl manifest. | Audit trail only. Dashboard today: bounds/ceiling per owl, **not** grants or pending prompts. | Channel prompt buttons (once/session/window/deny); `owl_build grant`; `batch_approve`; `/permissions`. | **No list of active grants, no approve/deny of a pending prompt, and no web prompter.** Auto-grant fallback applies to unknown channels (§4.3). |
| **Decision ledger** | `infra/decision_ledger.py`, `pipeline/decision_store.py:35`, bound at `pipeline/backends/shared.py:116-121` | `turn_decisions` (798; one row per session, upsert) | Yes. Dashboard today: no. | `/why`, `/explain` (both live-registered). | **Correction:** the component sweep reported `/explain`, `/cost` and `/tools` as unregistered. `load_builtin_commands` imports every `*_command.py` (`commands/assembly.py:8-10,140`), and the live boot log lists all three among 33 commands. No per-turn history, since it is upserted per session. |
| **Health / self-healing / incidents** | `health/aggregator.py:61`, `scheduler/handlers/health_sweep.py`, `incident_escalation.py`, `infra/resilience.py:95` (`HealableResource`), `learning/failure_outcome_miner.py` | Health snapshot and sweep alert state are **in memory**. Incidents exist only as `audit_log` event types (`incident.diagnosed`, `capability.self_healed`, `health.alerted` …). There is no incidents table. | Snapshot via core only; audit rows via DB. **Dashboard today: `/api/v1/health` snapshot.** | `health_sweep` and `incident_escalation` handlers; CLI `stackowl health`; `/owl health`. | **No time axis.** The rebuild commit records the sweep reporting unhealthy 49 times on 2026-09-12 while the route read 15/15 ok (`169d30f8` message). No incident timeline, no heal events shown. |
| **Cost / budgets** | `providers/cost_tracker.py:91` (emits `budget_exceeded`, `budget_80pct_alert`), `pipeline/budget/governor.py:32` (per-run caps, in memory), `BudgetSettings` (`config/settings.py:122`) | `cost_records` (134,455 rows) plus per-task accumulated cost on `tasks`. Governor state is in memory and raises are never persisted (`governor.py:9`). | DB yes. Dashboard today: `unattributed_spend` health row only. | `/cost`, `/config set budget.*`. | No spend-over-time or per-owl/per-provider cost view. |
| **Gateway / core split** | `runtime/gateway_link.py`, `ipc/server.py:66` (gateway binds), `ipc/client.py:28-52` (core connects), `ipc/frames.py`, `runtime/code_watcher.py` | Unix socket `~/.stackowl/runtime/core.sock` (`paths.py:189-197`); one peer at a time; newline-delimited JSON frames. Live: `stackowl start` (gateway) plus `stackowl __core__` [live]. | **No.** A second connector displaces the core link (`channels/dev_ingress.py:10-14`) [sweep]. Dashboard today: no. | Core drains and re-execs on code change (`orchestrator.py:4843,5053`); `/bye`. | No read-only query socket. No "process/restart" visualisation. `SteerFrame`/`StopFrame`/`QueryRunningFrame`/`RunningStateFrame` are defined and never sent or handled [sweep]. |
| **Logs** | `infra/observability.py:170-411` | `~/.stackowl/logs/stackowl.jsonl`, rotated daily to `stackowl-YYYY-MM-DD.jsonl`, 30-day retention. **All processes write one file**, with no `pid`/`role` field. | Yes (tail the file). Dashboard today: no. | Tool `read_logs`; `cli/trace_cli.py`. | Interleaved multi-process writes; no role field to split gateway from core. |
| **Config** | `config/settings.py:915`, `config/watcher.py:23` (5 s poll → `settings_reloaded`), `commands/config_helpers.py` | `~/.stackowl/stackowl.yaml`; secrets in keyring or 0600 files | Yes. **Dashboard today: `/api/v1/config`** (configured keys only, masked). | `/config` list/get/set/reset/export. | Read-only over HTTP. Shows configured, not effective, values (`server.py:1173-1177`). |
| **Audit log** | `audit/logger.py:90` (`chain_append_via_pool`, hash-chained, has `actor`) | `audit_log` (624) | Yes. Dashboard today: no. | `/audit`. | Only 2 `chain_append_via_pool` callers (`audit/deletions.py:112`, `scheduler/scheduler_helpers.py:127`). A web actor is never written, and the A05.1 design's plan to write the principal as `actor` was not built. |
| **Parliament / delegation** | `parliament/orchestrator.py`, `pipeline/durable/interactions.py` | `parliament_sessions` (0), `tasks.parent_task_id`, `side_effect_ledger` `delegate_task` | Yes. **Dashboard today: `/api/v1/interactions`.** | `/parliament`, `delegate_task`. | `parliament.completed` is emitted with no subscriber. The a2a mailbox is an in-memory queue logged at DEBUG only (`server.py:780-788`). |
| **Notifications / delivery** | `notifications/` | `notification_log`, `notification_queue`, `delivery_attempts`, `undelivered_outbox` | Yes. Dashboard today: no. | `/notifications`, `/quiet`, `/focus`, `/urgent`. | No delivery or outbox view. |
| **Webhooks** | `webhooks/receiver.py:205-214` | Listening on **127.0.0.1:8766**, enabled in this box's yaml [live]; `webhook_events_log` (0) | Yes. Dashboard today: no. | `/webhook`. | Per-source handler logic is a stub (`webhooks/handler_job.py:1-8`) [sweep]. |
| **MCP server** | `mcp/server.py:133-170` (starlette/uvicorn `GET /sse`) | Off by default (`mcp/server_settings.py`) | n/a | n/a | **Serves unauthenticated when no token is set** (`mcp/server.py:117-124`, cited as the counter-example in `control_plane/auth.py:11-16`). Its `publish()` call on EventBus is broken (§3.1). |
| **Voice** | `media/stt/local.py`, `channels/telegram/voice.py:196-300`, `tui/voice/recorder.py`, `tools/media/tts.py`, `media/tts/piper.py` | Local whisper `base`; piper `en_US-lessac-medium`; both installed [live] | n/a | Telegram voice note → transcript + confirm; TUI Ctrl+R; `tts` tool returns a file path. | No streaming STT, no spoken reply delivery, no browser audio path (§5). |

---

## 1. The current dashboard (`src/stackowl/control_plane/`)

### 1.1 Files and intent

| File | Lines | Role |
|---|---|---|
| `server.py` | 1,275 | `ControlPlaneServer(SupervisedTask)`: routes, guard, handlers |
| `page.py` | 925 | `INDEX_HTML`, `MANIFEST_JSON`, `ICON_SVG` as string constants |
| `auth.py` | 277 | credential mint/resolve, origin check, bearer auth, `ControlPrincipal` |
| `login_guard.py` | 103 | per-source failed-login counter |

**History:** 18 commits between 2026-09-11 (`6d71fdfd`, "a control plane that refuses to serve before it serves wrong") and 2026-09-12 (`b0c4f355`).

The latest redesign is `169d30f8` ("rebuilt from scratch — strips and one sheet, not ten tables"). It quotes the owner: *"Agentic os dahboard is bad. Redo it from scrach. Like jarvish style. Also make it look like mobile app not a website."*

That commit deliberately **refused** a node-link graph of agent interactions. Its reason: a third of the edges lack an end, and the newest delegation is 19 days old, so an animated graph "would claim live traffic that stopped". That is a documented design position a new "alive" UI must either honour or overturn with evidence.

**The design docs these files cite no longer exist.** `docs/reference-mapping/designs/A05.*.md` was deleted by `85ecdae4` (item-loop retirement). There are 15 `docs/reference-mapping` references left in `src/` docstrings, e.g. `server.py:3` and `auth.py:4`. The docs can be recovered with `git show 85ecdae4^:docs/reference-mapping/designs/A05.1.md`.

### 1.2 Server framework, launch, lifecycle

- **Framework:** aiohttp `web.Application` + `AppRunner` + `TCPSite(bind_address, port)` with **no `ssl_context`** (`server.py:167-185`). aiohttp is imported lazily (`server.py:87-91`).
- **Launch:** constructed in `StartupOrchestrator` only when `self._role != "gateway" and control_plane.enabled`. It is registered on the scheduler's `SupervisedTask` supervisor with the live `HealthAggregator`, `JobScheduler` and `DbPool` injected (`startup/orchestrator.py:4563-4584`).
  - In the split deployment this means the **core** process. It is confirmed live: pid 2155372 (`__core__`) owns `0.0.0.0:8787`.
  - The dashboard therefore goes away on every core re-exec (code watcher, `/bye`, crash), while the gateway and its channels stay up.
- **Blocking run:** `run()` waits on a stop event after binding, because returning would make the supervisor rebind and permanently park the task (`server.py:203-214`).
- **Settings** (`config/control_plane_settings.py:19-73`): `enabled=True`, `bind_address="0.0.0.0"`, `port=8787`, `username="admin"`, `password="admin"` (marked sensitive, hot-reloadable). The live yaml has **no** `control_plane:` section, so defaults apply [live].
- **CLI:** `stackowl control-plane [--json]` prints URL and token, resolving and never minting (`cli/app.py:1404-1470`). Two small defects:
  - It builds the URL from the bind address, so it prints `http://0.0.0.0:8787`, which is not an address a phone can open.
  - It decides loopback by string compare (`cli/app.py:1455,1463`), the same weaker duplicate rule `server.py:235-241` records fixing on the server side.
- **Reachability probe:** `health/reachability/probes.py:73-100` checks `enabled` and greps the orchestrator source for `ControlPlaneServer(`.

### 1.3 Routes

| Method | Path | Guard | Source of data | Live result (2026-09-12) |
|---|---|---|---|---|
| GET | `/` | none (constant page) | `INDEX_HTML` | 200 |
| GET | `/manifest.webmanifest` | none (constant) | `MANIFEST_JSON` | — |
| GET | `/icon.svg` | none (constant) | `ICON_SVG` | — |
| POST | `/api/v1/login` | origin + rate limit, no bearer | `control_plane.username/password` → returns the bearer token | — |
| GET | `/api/v1/health` | `_guard` | `HealthAggregator.collect()` (`server.py:1247`) | 200, 0.63 s, 15 subsystems all ok |
| GET | `/api/v1/agents` | `_guard` | `OwlStore.list_all`, `read_all_skill_ownership`, `read_owl_activity`, `effective_bounds` (`server.py:1004-1046`) | 11 owls, 7 unbounded |
| GET | `/api/v1/skills` | `_guard` | `read_all_skill_ownership` | 7 owning owls |
| GET | `/api/v1/tasks` | `_guard` | `read_task_activity` (unfinished only) | 5 live, 77 dead-lettered |
| GET | `/api/v1/schedules` | `_guard` | `JobScheduler.list_jobs()` + `read_run_history` | 170 jobs, 3,174 runs/24 h, 133 enabled silent (*corrected:* 136 are completed one-shot jobs still enabled; 33 live), 59 KB |
| GET | `/api/v1/memory` | `_guard` | `SqliteLessonsStore.recent(50)`, `counts_by_source`, `read_other_memory_counts`, `read_curated_entries` | 5,968 lessons, 19 curated targets, 50 KB |
| GET | `/api/v1/interactions` | `_guard` | `read_agent_interactions` | 69 edges, 9 without target, 15 without caller |
| GET | `/api/v1/config` | `_guard` | `config_path`/`load_yaml`/`collect_sensitive`/`flatten` (the `/config` command's helpers) | 45 keys, 7 masked |

A route whose collaborator is missing returns **503 with `wired: false`** rather than an empty 200, e.g. `server.py:495-501`.

### 1.4 Auth and access

- **Credential:** one bearer token, `secrets.token_urlsafe(32)`, resolved from the OS keyring and then from `~/.stackowl/secrets/stackowl-control-plane.key`, or minted and stored. The server **refuses to bind** without it (`auth.py:86-153`, `server.py:149-165`).
- **Guard order:** `Origin` must equal the request's own `Host` exactly; a missing `Origin` (curl) is allowed. Then the bearer token, compared in constant time. Then `principal.may(READ)` (`auth.py:175-277`, `server.py:374-416`).
  - It is an explicit per-handler call rather than middleware, **because aiohttp middleware does not run for WebSocket upgrades** (`auth.py:3-9`). The design anticipated a live channel.
- **Principal:** always `ControlPrincipal(principal_id=DEFAULT_PRINCIPAL_ID, granted={read,write,consequential})` (`auth.py:265-269`). `WRITE` and `CONSEQUENTIAL` are declared (`auth.py:43-46`) but no route uses them.
- **Login:** username and password are compared in constant time, both always compared (`server.py:920-925`). Rate limit is 10 failures per 300 s per `request.remote`, with at most 2,048 tracked sources (`login_guard.py:42-54`). `X-Forwarded-For` is deliberately ignored (`server.py:325-337`).
- **Boot warnings:** at WARNING when credentials are the default (`server.py:216-255`); live on 2026-09-12T17:07:38Z with `bind: 0.0.0.0` and `reachable_off_this_machine: true` [live]. The page also banners it after sign-in (`page.py:880-887`).
- **Token storage in the browser:** `sessionStorage` (per tab), never a cookie, and never the password (`page.py:861-889`).
- **Transport:** plain HTTP. On any non-loopback network the password and token travel in clear text.

### 1.5 Page technology and PWA

- **Build:** one constant string with no interpolation, enforced by tests. No framework, no CDN, no build step, one inline `<script>`. ES5-style vanilla JS with DOM construction through `textContent`, never `innerHTML` (`page.py:455-460`).
- **Model:** `PANELS` array → `Promise.allSettled` fetch of 8 routes → per-panel renderer → five bottom-rail destinations (Status/Fleet/Work/Mind/System) → a shared detail "sheet" (`page.py:538-547, 834-859`).
- **Visual system:** dark only, 7 colour tokens, monospace, 52 px "strip" rows, colour reserved for the 2 px status rail. `ok` is uncoloured; a hatch means unknown and a dashed rail means unbounded (`page.py:103-318`).
- **Mobile:** safe-area insets, 16 px input floor to avoid iOS zoom, 44 px touch targets, `100dvh`, `prefers-reduced-motion` honoured.
- **Liveness:** none. The "fresh" bar animates once per load and prints the load time (`page.py:827-832`). **No refresh**: a reload or re-sign-in is the only update.
- **PWA:** `manifest.webmanifest` (`display: standalone`, relative `start_url`, one SVG icon "any maskable", `page.py:54-67`), `apple-mobile-web-app-*` meta tags (`page.py:95-100`). **No service worker**, no offline shell, no raster icons. Chromium's install prompt and service workers both require HTTPS off localhost.

### 1.6 Verified defects in the page (real, not cosmetic)

These were checked against live payloads [live] and the page source [code]:

1. **Memory panel reads fields the route does not send.** `renderMemory` reads `lesson.source` and `lesson.at` (`page.py:727-730`). The route emits `source_type` and `created_at` (`server.py:743-751`), and the live payload keys are `lesson_id, source_type, source_ref, content, created_at`. The strip subtitle falls back to `source_ref`, and the sheet's "source" and "at" are always an em-dash.
2. **Curated memory renders as "[object Object]".** `strip(host, "ok", cur.target, "curated", String(dash(cur.entries).length), "chars", [["entries", cur.entries]])` (`page.py:722-724`) stringifies an **array of objects** (confirmed `list` in the live payload). The metric is the length of `"[object Object],[object Object],…"`, and the sheet shows that string instead of the curated text: the most load-bearing memory on the platform.
3. **Agent bounds and ceilings render as "[object Object]".** `["bounds", owl.bounds]` and `["creation ceiling", owl.creation_ceiling]` pass dicts through `dash()` → `String()` (`page.py:461-463, 626`). This affects the 4 bounded owls, which are exactly the ones whose authority a reader wants to see.
4. **Why the guard misses #1.** `test_the_page_reads_no_field_a_route_does_not_emit` builds its "emitted" set from **every** string-keyed dict literal in **every** `_handle_*` method (`tests/control_plane/test_the_dashboard_is_a_page_a_person_can_open.py:325-335`). That includes log `extra={"_fields": {...}}` dicts, e.g. `"source": source` at `server.py:900`, and other routes' keys (`"at"` from interactions, `server.py:830`). The check is page-global, not per route, and it does not check object-valued fields at all.

### 1.7 Guard tests (`tests/control_plane/`, 13 files)

They pin:
- fail-closed credential handling (no bind without token; no default principal on the request path)
- every handler goes through `_guard`, with no middleware and no own auth
- origin-before-token order, uniform 401 body, no token in logs
- LAN-Host acceptance on a wildcard bind
- login semantics (constant time, config-sourced, rate limit bounded)
- page is a constant, handler reads no state, no cookies, no external hosts, valid JS, no homoglyphs
- route↔page bijection on `/api/` paths, plus field reads (weak, see §1.6)
- one-dead-route-does-not-blank-page, resume from `sessionStorage`
- manifest/icon unguarded and stateless
- each route's payload semantics (e.g. `blocked` reason, unwired → 503, owner scoping, no merged "memories" number)

A new app must either keep or consciously retire each of these invariants. Several are security properties, not style.

---

## 2. (Component inventory: see the table above)

---

## 3. Live signal sources

### 3.1 EventBus (`events/bus.py:25`)

- **API:** `subscribe`/`unsubscribe`/`emit` only, with no `publish` method.
- **Handlers:** sync handlers run inline; async handlers are scheduled as tasks. Nothing is persisted.
- **Scope:** one bus per process (`startup/orchestrator.py:1255`).

| Event | Emitted | Subscribed | Status |
|---|---|---|---|
| `settings_reloaded` | `config/watcher.py:120`, config/tier/webhook/provider commands | provider, identity and tool-cap reload (`orchestrator.py:1755-1769`), webhook (`:4609`), auto-restart (`:4704`) | wired |
| `session.rollover` | `sessions/store.py:420` | rollover summary, conversation cost report | wired |
| `budget_exceeded`, `budget_80pct_alert` | `providers/cost_tracker.py:397,415` | `notifications/event_bridge.py:116`, TUI | wired |
| `conversation_cost_report` | `providers/conversation_cost_report.py:92` | event bridge | wired |
| `consent.confined_execution_granted` | `tools/consent.py:139` | event bridge | wired |
| `pipeline_step_changed` | `pipeline/progress/emitter.py:126` | `tui/coordinator.py:113` | mono only; **no subscriber in core** under split |
| `response_chunk`, `compose_submitted` | `channels/cli_adapter.py`, `tui/app.py` | TUI / CLI adapter | wired (gateway/mono) |
| `parliament.completed`, `owl_edited`, `owl_removed`, `focus_mode_changed`, `parliament_suggestions_unsuppressed`, `morning_brief_rendered` | various commands/handlers | **none** | emitted, never subscribed |
| `mcp_spectator_active`/`_disconnected` | `mcp/server.py:248,266` via `.publish(...)` | TUI | **broken**: `EventBus` has no `publish` |
| `provider_degraded`, `job_paused`, `parliament_*` (5), `synthesis_arrived`, `memory_fact_updated`, `evolution_batch_complete`, `toast_request` | **nowhere** | `tui/coordinator.py:27-43` | subscribed, never emitted |

**Cross-process:**
- `ProgressEventFrame` (core→gateway) is defined (`ipc/frames.py:189-199`), and the gateway re-emits it (`runtime/gateway_link.py:448-450`), but **no code constructs it** (zero call sites) [code].
- Progress chunks do cross the socket as `ChunkFrame kind="progress"` (`ipc/stream_bridge.py:48`), but `cli_adapter.py:204-207` drops them.
- Result: in split mode the TUI pipeline strip gets no live progress [sweep].

**What the bus does not carry at all:** tool call start/end, model call start/end, task claimed/completed, job run start/end, consent requested/decided, health changes, heal events, memory writes, delegation hops. These are logged (jsonl) or persisted (rows), not evented.

### 3.2 jsonl logs

- **Location:** `~/.stackowl/logs/stackowl.jsonl`, rotated at UTC midnight to `stackowl-YYYY-MM-DD.jsonl`, 30-day retention (`infra/observability.py:359-411`).
- **Record:** `ts, level, module, msg, trace_id, span_id, parent_span_id, session_key, conversation_id, duration_ms, fields` (`observability.py:170-195`).
- **Writers:** gateway, core and CLI all write **the same file** (`cli/app.py:113,145,247,274`), with no `pid`/`role` field. The rotation-follow fix is at `observability.py:285-332`.
- **Level:** INFO in production. The source repeatedly notes zero DEBUG records, so DEBUG-level facts (e.g. a2a mailbox hops) are unobservable.
- **Potential:** the richest live signal today. Structured, with trace and span ids, and written by every process. Tailing it is the cheapest route to a live feed, but it is a log, not a contract.

### 3.3 Tables that change per turn [sweep]

| Table | Writer |
|---|---|
| `message_ledger` | `memory/message_ledger_store.py:129` (pending → completed) |
| `conversations`, `messages` | `memory/transcript_store.py:116,128` |
| `sessions` | `sessions/store.py:436,606` |
| `turn_decisions` | `pipeline/decision_store.py:26` (upsert per session) |
| `cost_records` | `providers/cost_tracker.py:300` (per provider call) |
| `task_outcomes` | `memory/outcome_store.py:296` |
| `session_prompts` | `sessions/prompt_store.py:217` (on prompt rebuild) |
| `tasks`, `side_effect_ledger` | `pipeline/durable/store.py`, `durable/ledger.py` |
| `channel_liveness` | `channels/liveness.py:35` |
| `staged_facts` | `memory/sqlite_bridge.py:439` |

There is no dedicated tool-call table. SQLite has no change notification across processes, so these can only be **polled**.

### 3.4 Existing browser-subscribable streams

**None.** The existing streams are:
- control plane: request/response only
- webhook receiver: inbound POST only (`webhooks/receiver.py:206`)
- MCP `GET /sse`: MCP protocol for tool clients, core only, off by default (`mcp/server.py:133-170`)

A search finds no `text/event-stream`, `EventSourceResponse`, `WebSocketResponse` or FastAPI.

### 3.5 What "every action visualised live" would need that does not exist

1. **A typed platform event stream.** Tool, model, task, job, consent, health, heal, memory, delegation and channel-delivery events need to be emitted at the point of action with trace and span ids. The bus has almost none of these, and the jsonl has them only as free-text `msg` plus `fields`.
2. **A cross-process path.** Either `ProgressEventFrame` (or a successor) actually sent core→gateway, or a core-side broadcaster.
3. **A browser transport** (SSE or WebSocket) with auth that works for upgrades. `auth.py` was designed with this in mind, but no ticket or session store exists.
4. **Replay/backfill,** so a phone that reconnects sees what happened while it slept. There is no event log table; `audit_log` is sparse (624 rows).
5. **A time axis** on health, cost and jobs. Every current route is a snapshot.

---

## 4. Control surface

### 4.1 Slash-command registry (`commands/`)

- **Model:** `SlashCommand` (`base.py:12`) with `handle(args, state) -> str | CommandResponse`, plus optional `CommandMeta`/`SubCommand` tree for help and autocomplete (`metadata.py:77-126`).
- **Registration:** singleton `CommandRegistry` (`registry.py:16-43`). `register_all_commands` (`assembly.py:109-150`, called at `orchestrator.py:2094`) imports every `*_command.py` (pattern A) and constructs DI commands (pattern B, `assembly.py:185-392`).
- **Live registered set (33)** [live log 2026-09-12T17:07:37Z]: `audit brief browser bye config connect cost disconnect explain find focus help learn memory new notifications onboarding owl parliament permissions plugins preferences provider quiet reset skill style tier tools urgent webhook whoami why`.
- **Channel-agnostic:** `dispatch(name, args, PipelineState)` has no channel dependency (`registry.py:60-96`). Replies carry `actions: tuple[Action(label, command, destructive)]` (`commands/response.py:23-32`), rendered as buttons whose tap replays the command. That is a ready-made control vocabulary.
- **No per-command authorisation.** `dispatch` checks nothing [code]. The only gates are:
  - channel ingress allow-lists (Telegram `is_authorized`, `press_is_authorized` in `channels/callback_authz.py:42`)
  - a second tap for `destructive` actions
  - `consent_gate` for `/skill migrate` only
- **`SubCommand.handler` drive-mode dispatch** is declared but never used by `dispatch` [sweep].
- **Dry run:** a trailing `??` previews without running (`registry.py:69-83`), a safe primitive for a "what would this do" UI.

### 4.2 IPC gateway ↔ core

- **Transport:** unix socket `~/.stackowl/runtime/core.sock`. The gateway binds (`ipc/server.py:66`) and the core connects with retry (`ipc/client.py:28-52`). One peer at a time. Newline-delimited JSON with a discriminated union on `type` (`ipc/codec.py:22-39`).
- **Frames** (`ipc/frames.py`):
  - gateway→core: `ingress, consent_response, ephemeral_sent, clarify_reply, steer, stop, query_running`
  - core→gateway: `hello, goodbye, restart_notice, chunk, send_text, send_file, send_ephemeral, delete_message, progress_event, clarify_ask, consent_request, running_state`
  - both: `ack`
- **Unwired frames:** `steer`, `stop`, `query_running`, `running_state` and `progress_event` (never sent); `clarify_reply` (never sent by the gateway) [sweep + code for progress_event].
- **Core restart:** core sends `restart_notice`, the gateway buffers, the core re-execs, in-flight turns are re-pended, and on `hello` they are replayed with the same trace_id up to 3 attempts (`runtime/gateway_link.py:191-210, 296-350, 460-467`).
- **`dev-ingress.sock`:** a separate 0600 unix socket for injecting a turn into a live channel. Telegram only (`channels/dev_ingress.py:98-260`, `orchestrator.py:3700-3712`).

### 4.3 How consent and authz would apply to a web caller

1. **Unregistered channel means auto-grant.** `RoutingPrompter.prompt` routes an unknown `req.channel` to `AutonomousPrompter`, which grants ordinary consequential actions (`tools/consent.py:664-685`) [code]. Always-ask categories are applied earlier by `ConsentPolicy`.
2. **Core registers the socket prompter for exactly five names**: `cli, telegram, slack, discord, whatsapp` (`orchestrator.py:1496-1502`) [code]. A turn submitted on channel `web` in split mode would be auto-granted.
3. **The address is lost in split mode.** `ConsentRequestFrame` has no `reply_target` (`ipc/frames.py:225-242`) [code], so the gateway rebuilds `ConsentRequest` with no address. Telegram then falls back to parsing a chat id from the `session_key` tail (`channels/chat_id.py:35-57`) [sweep].
4. **Web callers have no authority model.** The control-plane principal is always the default owner with all severities. The platform's own authority model (owl `bounds` ∩ `creation_ceiling`, `authz/bounds_guard.py`) governs **agents**, not **humans**.
5. **Minimum for a safe web control path:**
   - a `web` `ConsentPrompter` on the gateway and core registration of `web`
   - `reply_target` on the frame
   - per-endpoint severity checks using the existing `ControlPrincipal.may()`
   - audit writes with the web principal as `actor`
   - mutations enqueued as tasks rather than run inline. The deleted A05.1 design specified "creates a task row and returns 202", mirroring `WebhookReceiver._parse_and_enqueue`.

**Actions that could be exposed with the least new risk** (each already has a single existing implementation to call, not reimplement):
- job pause/resume/run-now (`JobScheduler`)
- task retry/cancel (store methods exist; no command does it)
- skill enable/disable (`SkillIndexStore.set_enabled`)
- config set via the `/config` helpers
- owl pause/resume (`owl_build`)
- command dry-run (`??`)

**Actions that must stay consent-gated:** `/bye`, `/provider` add/remove, `/connect`, `owl_build grant`, `/memory forget`, `/cost privacy`.

### 4.4 Other HTTP servers

| Server | Bind | Default | Routes |
|---|---|---|---|
| Control plane (aiohttp) | 0.0.0.0:8787 | **on** | §1.3 |
| Webhook receiver (aiohttp) | 127.0.0.1:8766 | off (**on in this yaml** [live]) | `POST /webhook/{source}` HMAC → one-shot job |
| MCP (starlette/uvicorn) | 127.0.0.1:8765 | off | `GET /sse`, tools only, unauthenticated without token |
| Google OAuth callback (`http.server`) | localhost, temporary | on demand | redirect |

---

## 5. Conversation and voice today

### 5.1 How a message enters

1. `ChannelAdapter.receive()` (`channels/base.py:31-91`) → `_message_loop` (`orchestrator.py:3408-3428`) → `turn_client.submit(msg)`.
2. In split mode `GatewayLink._do_submit` registers a `StreamDemux` reader by `trace_id`, starts `adapter.send(reader)`, and sends an `IngressFrame` (`runtime/gateway_link.py:225-246`).
3. Core `_core_frame_loop` → `SocketChannelAdapter` → `_handle_ingress` → scanner → `_dispatch_turn` (`orchestrator.py:3446-3490, 2542`). A leading `/` routes to command dispatch (`gateway/scanner.py:399-406`).

- **Message shape:** `IngressMessage(text, session_key, channel, trace_id, chat_id, is_reply, is_direct)` (`gateway/scanner.py:91-116`).
- **`scripts/dev_ingress.py`:**
  - What it sends: one JSON line to `~/.stackowl/runtime/dev-ingress.sock` with `channel: telegram`, the owner's chat id and the text.
  - What it gets back: only `{ok, trace_id}` within 10 s. **The answer goes to the real Telegram chat, not to the caller** (`scripts/dev_ingress.py:40-71`).
  - Server side it is allow-listed through Telegram's `is_authorized` and pushed onto the live adapter queue (`channels/dev_ingress.py:236-245`).
- **For a web conversation surface:**
  - What exists: an ingress path that returns a stream (`adapter.send(reader)`).
  - What does not: a channel that owns the reader end.

### 5.2 How answers and progress stream back

- **`ResponseChunk`** (`pipeline/streaming.py:22-58`): `content, is_final, chunk_index, trace_id, owl_name, duration_ms, kind: "answer" | "progress"` (only two kinds), `target, is_floor, actions, raw_keyboard, display_suffix`. It is terminated by `is_final=True, chunk_index=-1`.
- **In split mode:** chunks cross as `ChunkFrame` field for field (`ipc/stream_bridge.py:39-54, 103-123`).
- **Progress:** `pipeline/progress/emitter.py:122-165` emits both a bus event and a `kind="progress"` chunk. The payload is only `{step_name, step_index, total_steps}`, with no tool names or reasoning.
- **Telegram rendering:** progress goes to `TelegramProgressView` (one edited status message, rate-limited, typing ticker, "done in Ns" footer; `channels/telegram/progress_render.py`). Answers are buffered then sent with inline keyboards (`telegram/adapter.py:514-561`).
- **Clarification questions** are sent as numbered plain text via `send_text` (`gateway_link.py:471-485`).

### 5.3 Voice

**Speech-to-text:**
- **Engine:** local `openai-whisper`, model `base`, loaded lazily in an executor (`media/stt/local.py:1-46,101`). The cloud STT backend is a placeholder that is always unavailable (`media/stt/selector.py:49-72`).
- **Setting:** `transcription.enabled` defaults `True` (`config/settings.py:281-296`).
- **Live:** boot log "Telegram voice transcription enabled" at 2026-09-12T17:07:20Z; `whisper`, `ffmpeg`, `arecord`, `parecord` and `sox` are installed. `faster_whisper` is not installed.
- **Telegram:** OGG download → transcribe → transcript shown with confirm/discard buttons → confirmed text enqueued (`channels/telegram/voice.py:196-300`).
- **TUI:** Ctrl+R push-to-talk via an external recorder (`tui/voice/recorder.py`, wired `orchestrator.py:2343-2359`).
- **Gaps:** whole-file transcription only, with no streaming or partial results, no VAD/endpointing, no browser audio ingest.

**Text-to-speech:**
- **Where it exists:** only as the agent tool `TtsTool` (`tools/media/tts.py`) via `TtsSelector`.
- **Backends:** local `PiperBackend` (default voice `en_US-lessac-medium`, voices fetched from HuggingFace; `media/tts/piper.py:42-44`, `TtsSettings` at `config/settings.py:212-260`). Cloud `{base_url}/audio/speech` is opt-in and off.
- **Output:** a **file path**. `piper` is installed [live].
- **Gaps:** no `send_voice`/`send_audio` anywhere, and no automatic spoken reply on any channel. Telegram `send_file` sends non-images as documents.

**For two-way web voice, nothing exists between the browser microphone and the pipeline, or between the pipeline and a speaker.** The engines (local whisper, local piper) do exist and are installed.

---

## 6. Identity and multi-user

- **Tenancy is schema-deep and runtime-shallow.**
  - `tenancy/principal.py:25` defines `DEFAULT_PRINCIPAL_ID = "principal-default"`. Migration `0042` creates `principals` (1 row). `0043` adds `owner_id NOT NULL DEFAULT 'principal-default'` to ~16 tables, and later tables carry it too.
  - `OwnedRepository` (`tenancy/owned_repository.py:53`) scopes queries for `DurableTaskStore`, `SideEffectLedger`, parliament `SessionStore` and learning stores.
  - **`PrincipalStore` has no importer outside `tenancy/`**, so nothing creates or resolves a second principal.
  - There is no `tenant_id`.
- **Cross-channel identity is an alias map, not accounts.**
  - `IdentitySettings.aliases` maps identity key → channel handles (`config/settings.py:831-843`, empty by default).
  - `IdentityResolver.resolve` (`tenancy/identity.py:13-43`) is used as `state.identity_key or state.session_key` (`pipeline/services.py:364-438`), and hot-reloads on `settings_reloaded`.
  - `stackowl identity link` is a one-off SQL rewrite hardcoded to the default principal (`cli/identity_cli.py:21-102`).
- **Dashboard login is unrelated to either.** It uses one shared username and password from config, one shared bearer token, and principal = default owner (`auth.py:219-277`). `auth.py:228-231` reserves a `principal_id` keyword "so a later credential store can supply the real owner". That store does not exist.
- **Nothing a web login could reuse:** no users table, password hashing, session or refresh tokens, per-device revocation, or mapping from a web user to a `principal_id` or an `identity_key` alias. Using `IdentitySettings.aliases` (e.g. `web:<device>`) is the smallest bridge. It would make a web session the same person as the Telegram owner for memory and history purposes.

---

## 7. Earlier agentic-OS work: what landed, what didn't

- **Branches:** only `main` and `origin/main` exist.
- **`feat/agentic-os-stage1` was merged, not abandoned.** Merge `3467565f` (2026-06-13) "land reliability-spine epic (agentic-os-stage1) to main", 73 commits. The packages were authored under `v2/src/stackowl/` and moved to the root by `c72a1704`, which is why `--diff-filter=A` on today's paths shows nothing from June.
- **All three packages are on `main` and reached from startup:**
  - `tenancy/`: §6.
  - `pipeline/durable/` (23 modules): `DurableTaskStore` at `orchestrator.py:1022`, `TaskLoop` at `:1940-1950`, recovery at `:4514-4536`. The control plane reads `durable.activity` and `durable.interactions`.
  - `authz/` (`bounds.py`, `bounds_guard.py`, `enforcement.py`): used by execute, planner, budget governor, owl builder and the control plane. The retired programme's record says only the `tools` axis is enforced. Not re-verified here.
- **"Mission Control" was never an epic or document.** The only real occurrence is the manifest description "Mission control for a self-hosted kernel of persistent agents." (`control_plane/page.py:57`). The `grep` hit in `docs/superpowers/specs/2026-06-05-budget-governor-design.md` is "spawn-admission control".
- **September "Agentic OS" item programme (A01–A07):**
  - **Seeded** 2026-09-10 (`8e62ddbb`).
  - **Built:** A01.2, A04.1, A05.1–A05.8 read halves, A05.10 (the dashboard).
  - **Retired** on 2026-09-12 by `85ecdae4`, which deleted `progress.yml`, the item-loop skill and 94 design docs.
  - **Never built:** A02 (secret scoping/egress), A03 (confinement), A04.2, A05.2/A05.8 write halves, A06 (memory), A07 (catalogue), and the A05.1-planned `ControlEndpoint`/`EndpointRegistry`, task-row-returning write endpoints, audit `actor` attribution, and the live channel with its ticket store.
- The root `CLAUDE.md` was deleted in `34e022da` ("clenup", 2026-09-12).

---

## 8. Hardware and deployment constraints

- **Host:** `boss-desktop`, NVIDIA Jetson, Linux 5.15.148-tegra, aarch64, 6 cores, 7.4 GiB RAM, **3.6 GiB available**, 3.7 GiB swap with ~1.3 GiB used [live]. It also hosts whisper, piper, a playwright browser, Kuzu and the Python processes. The README says the project is "developed on an NVIDIA Jetson" (`README.md:17`).
- **Launch on this box:**
  - User crontab `@reboot` → `start.sh` → `nohup uv run python -m stackowl start` (`start.sh:111`) [sweep]. No stackowl systemd unit is running.
  - The repo also ships `deploy/stackowl.service`, `deploy/com.stackowl.plist`, `deploy/install-service.ps1` and a `Dockerfile`.
  - Split process: gateway (`stackowl start`) plus core (`stackowl __core__`), talking over `core.sock`.
- **Reaching the dashboard from a phone:** only on the same LAN, over plain HTTP (e.g. `http://192.168.1.160:8787`; wired `enP8p1s0` 192.168.1.160 and wifi 192.168.1.189 [sweep]).
- **No off-LAN or HTTPS story:**
  - No TLS option (`TCPSite` without `ssl_context`, `server.py:183`).
  - No reverse proxy config (nginx/caddy/traefik), no tunnel (tailscale, cloudflared and ngrok are not installed), no trusted-proxy handling (`server.py:325-337`).
  - `tun0` is a corporate GlobalProtect client VPN unrelated to the platform.
- **Browser-platform consequences of plain HTTP on a LAN IP** (web-platform rules, not StackOwl code):
  - `navigator.mediaDevices.getUserMedia` (microphone), service workers, Web Push and the Chromium install prompt are unavailable, because the page is not a secure context.
  - Therefore both voice mode and a real installable/offline PWA require HTTPS (or a tunnel that terminates TLS) before any UI work matters.
- **Process placement matters for "alive":**
  - The control plane lives in the core, which re-execs on code change. Any live socket to the browser drops on every core restart.
  - The gateway is the durable process but holds no platform state beyond channels.

---

## Hard facts the design must respect

1. **One owner, one principal.** Every row is `owner_id = 'principal-default'`; `ControlPrincipal` is always that owner with read/write/consequential. A "login" today is a shared password, not a user.
2. **The control plane runs in the core, not the gateway** (`orchestrator.py:4563`), and the core re-execs on every code change. Anything long-lived to the browser must survive or re-establish across that.
3. **Gateway↔core is a single-peer unix socket.** A web server cannot attach to `core.sock` as a second client without displacing the core. Any new query or command path is a new frame type or a new socket.
4. **SQLite in WAL mode is the shared truth** and is readable concurrently (`workspace/stackowl.db`, not `~/.stackowl/stackowl.db`). Consent grants, live health, tool catalogue, provider breakers/windows, TaskLoop leases and governor state are **in core memory only**.
5. **Consent grants are deliberately not persisted** (`tools/consent.py:728-744`). A dashboard may show them live but must not make them durable.
6. **Unknown consent channels auto-grant** (`tools/consent.py:664-685`). A new `web` channel is unsafe until it has a registered prompter in both processes.
7. **Command dispatch has no authorisation** (`commands/registry.py:60-96`). Exposing `dispatch` over HTTP exposes `/bye`, `/config`, `/provider` and `/connect` to anyone holding the token.
8. **The existing auth invariants are pinned by tests:**
   - fail closed without a credential
   - origin checked before token, and origin compared to `Host`
   - per-handler auth, not middleware, because WebSocket upgrades bypass middleware
   - uniform 401 body; no token in logs
   - page constant with no platform data; token in `sessionStorage`, not cookies
   - no third-party hosts

   "No CDN, no framework, no build step" is also a stated self-hosting requirement (`page.py:29-32`).
9. **Snapshots are not verdicts.** The 2026-09-12 rebuild measured the health route reading 15/15 ok while the sweep logged unhealthy 49 times that day. Every current route lacks a time axis.
10. **Some "alive" imagery would be false today.** Delegation traffic stopped 2026-08-24, 9 of 69 edges have no target, 133 of 170 enabled jobs did not run in 24 h (*corrected 2026-09-12:* 136 of the 169 enabled jobs are one-shot `rollover_summary` jobs that completed but were never switched off (status `completed`, `enabled=1`, next run stuck in the past) and only the 33 others are live schedules — verified against the `jobs` table 2026-09-12), and 77 tasks are dead-lettered. A UI that animates "activity" must derive it from measured events, or it will depict a platform that is not there. This is the explicit reason `169d30f8` refused a node-link graph.
11. **Only two response-chunk kinds exist** (`answer`, `progress`), and progress carries only step name/index/total.
12. **Plain HTTP on a LAN** means no microphone, no service worker, no push and no install prompt in mobile browsers.
13. **The box is a 7.4 GiB Jetson already running whisper, piper, a headless browser and the platform.** Server-side work for the UI (fan-out, rendering, audio) competes with the agents for the same memory.
14. **Owner rules recorded in memory:**
    - one loop, never a second engine: mutations should become tasks
    - fix the platform, not this setup: defaults must work for a fresh clone
    - capabilities ship enabled
    - no vendor names in `src/`
    - cross-platform
    - all state under `~/.stackowl/`
    - SQLite/markdown only, one copy of each fact

---

## Gaps a real agentic-OS dashboard would require

**Transport and security**
1. **HTTPS or TLS-terminating remote access,** with a trusted-proxy rule if proxied. It is a prerequisite for phone voice, service worker, push and install.
2. **Real web identity:** user record(s), hashed passwords or passkeys, session tokens with expiry and revocation, and mapping a web principal to `principal_id` and an `identity_key` alias. Default credentials should not be the shipped state on a `0.0.0.0` bind.

**Live signals**

3. **A typed event spine,** covering tool/model call start/end, task claim/finish, job run, consent requested/decided, health transition, heal action, memory write, delegation hop, and channel delivery. It must be emitted at the action site with trace ids and persisted to a bounded event table for replay.
4. **Cross-process delivery of those events.** Wire `ProgressEventFrame` (or a successor) core→gateway, or broadcast from core, and fix the dead or broken bus names (`publish` in `mcp/server.py`, emitted-never-subscribed and subscribed-never-emitted sets).
5. **A browser stream endpoint** (SSE or WebSocket) with per-handler auth, reconnection with a resume cursor, and backpressure.
6. **A time axis:** health history, cost over time, job run timelines, task history (terminal rows included).

**Control**

7. **A write/control API** built on the existing implementations, enforcing `ControlPrincipal.may(WRITE|CONSEQUENTIAL)`. Mutations should be enqueued as tasks (202 + task id) and audited with the web principal as `actor`. Candidates: job pause/resume/run, task retry/cancel, owl pause/resume, skill enable/disable, config set, command dry-run.
8. **Per-command authorisation** in `CommandRegistry.dispatch`, or a web-safe allow-list of commands with severities, before any generic "run a command" affordance.
9. **Consent in the web app:** a `web` `ConsentPrompter` registered on gateway and core, `reply_target` added to `ConsentRequestFrame`, a list of pending prompts and active session/window grants from core memory, and approve/deny from the browser with authorisation of the approver.
10. **A core query path** for in-memory state: tool catalogue, grants, live health, breakers, context windows, worker occupancy, a2a mailbox.

**Conversation and voice**

11. **A `web` conversation channel:** an adapter that owns the reader end of the response stream, supports `send_text`/`send_file`/`send_ephemeral`/`delete_message`, renders `actions` as buttons, and is added to `configured_gateway_channels`. Plus a transcript/session browser over `conversations`/`messages`.
12. **Voice pipeline for the browser:**
    - microphone capture → streaming or chunked STT with endpointing (today: whole-file local whisper `base`)
    - an automatic spoken-reply path (today: piper exists only as a tool returning a file; no channel sends audio)
    - barge-in via the never-wired `steer`/`stop` frames

**Hygiene**

13. **Survive core restarts:** decide where the web server lives (gateway is durable; data lives in core) and how the browser reconnects across `os.execv`. Placement is an owner vote, not a default.
14. **Fix the current page before anything builds on it:**
    - memory reads non-emitted `source`/`at`
    - curated entries, bounds and ceilings stringify to "[object Object]"
    - tighten the field-read guard to per-route emitted keys, excluding log `extra` dicts
    - `stackowl control-plane` prints `0.0.0.0` as a URL
15. **Restore or relocate the design rationale.** Docstrings in `src/` cite 15 paths under the deleted `docs/reference-mapping/`.
