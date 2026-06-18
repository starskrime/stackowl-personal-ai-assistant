# Cycle 4 — Multi-Agent Runtime Primitive

**Date:** 2026-05-12
**Owner:** Bakir
**Status:** Approved (sections 1-8 confirmed via brainstorming dialogue)

## Goal

Close the biggest remaining competitive gap vs OpenClaw: turn `orchestrate_tasks` (fire-and-forget) into a real multi-agent runtime where spawned subagents outlive the conversation that created them, can be queried by id later, can receive messages mid-run, and can be terminated explicitly.

Six LLM-callable tools, one SQLite-backed `SessionStore`, one in-process `SessionRunner`, all built on top of the existing `OwlEngine` + Platform layer.

## Non-goals

- **Trust scopes / per-spawn tool allowlists** — per the explicit user decision, subagents inherit the parent's full tool registry (no restriction). Future cycle can add an optional `allowed_tools: string[]` param without breaking the contract.
- **Parliament refactor** — Parliament keeps its current implementation. Subagents is a separate primitive.
- **Cross-process / distributed sessions** — all sessions live inside the single StackOwl Node process; SQLite DB is per-workspace.
- **Replay / rewind** — sessions terminate or complete once; no "rewind to turn N" semantics.
- **Web/CLI UI** — only the 6 LLM-callable tools in v1.

## Architecture

```
src/sessions/
├── types.ts             # Session, SessionMessage, SessionStatus interfaces
├── store.ts             # SQLite-backed persistence (sessions + session_messages tables)
├── runner.ts            # SessionRunner — lifecycle, message routing, hydration
└── engine-host.ts       # Fresh OwlEngine per session, isolated history, shared tool registry

src/tools/sessions/      # 6 new LLM-callable tools
├── subagents.ts          # Spawn N background sessions
├── sessions-status.ts    # Get status + messages
├── sessions-send.ts      # Send a message to a running session
├── sessions-yield.ts     # Block until response / state change / timeout
├── sessions-list.ts      # Enumerate sessions (filterable)
└── sessions-terminate.ts # Kill a session

src/memory/db.ts         # MODIFIED: +sessions, +session_messages tables
src/engine/runtime.ts    # MODIFIED: +AbortSignal in EngineContext
src/index.ts             # MODIFIED: wire SessionRunner; register 6 tools
docs/dev-setup.md        # MODIFIED: brief subagents-vs-orchestrate_tasks section
```

### Unchanged

- `orchestrate_tasks` keeps its synchronous fan-out/fan-in semantics. Distinct use case from `subagents` — see §3.
- Parliament unchanged. Future cycle may refactor onto subagents.
- `OwlEngine`, `ToolRegistry`, `MemoryDatabase` — reused, not modified (except `EngineContext` gains `signal?: AbortSignal`).

### Design principles

- **Inherit-everything trust** — subagents see the same `toolRegistry` the parent saw, including write-capable and shell tools. User decision; documented here as deliberate.
- **Persistence-first** — every state change hits SQLite immediately. No in-memory-only state for `Session.status` or messages. Sessions survive process restarts.
- **Async by default** — `subagents` returns immediately with session IDs; `sessions_yield` is the only blocking surface, with hard timeouts.
- **4-point logging** — entry / decision / step / exit on every lifecycle hop, every tool call.

## Session lifecycle

```
                    ┌─────────┐
       spawn()  →   │ pending │ ─── runner picks up ──┐
                    └─────────┘                       ▼
                                                ┌─────────┐
                          sessions_send() ──────│ running │
                                                └────┬────┘
                                                     │
                       ┌─────────────────────────────┤
                       ▼                             ▼
                ┌──────────────┐              ┌───────────┐
                │awaiting_input│              │ completed │ (terminal)
                └──────┬───────┘              └───────────┘
                       │
                       ▼ sessions_send arrives
                  ┌─────────┐
                  │ running │
                  └─────────┘

  At any state, sessions_terminate() → terminated (terminal)
  At any state, internal exception   → failed (terminal)
```

States:
- **pending** — created but not yet picked up by the runner
- **running** — runner is driving an `OwlEngine.run()` call
- **awaiting_input** — engine emitted output and is waiting for a `to_session` message
- **completed** — engine finished cleanly; `result` field populated
- **terminated** — explicit `sessions_terminate()`
- **failed** — engine threw; `error` field populated

`pending`, `running`, `awaiting_input` are non-terminal. `completed`, `terminated`, `failed` are terminal — no further state transitions.

## SQLite schema

Added to `MemoryDatabase.createSchema()`:

```sql
CREATE TABLE IF NOT EXISTS sessions (
  id              TEXT PRIMARY KEY,
  parent_id       TEXT,
  status          TEXT NOT NULL CHECK(status IN ('pending', 'running', 'awaiting_input', 'completed', 'terminated', 'failed')),
  prompt          TEXT NOT NULL,
  history_json    TEXT,
  result          TEXT,
  error           TEXT,
  metadata        TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  terminated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

CREATE TABLE IF NOT EXISTS session_messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL,
  direction    TEXT NOT NULL CHECK(direction IN ('to_session', 'from_session')),
  content      TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  consumed_at  TEXT,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_messages_pending ON session_messages(session_id, consumed_at);
```

Idempotent `CREATE TABLE IF NOT EXISTS` — additive, no destructive migration.

## `SessionStore` API

```typescript
export interface Session {
  id: string;
  parentId: string | null;
  status: "pending" | "running" | "awaiting_input" | "completed" | "terminated" | "failed";
  prompt: string;
  history: ChatMessage[];
  result?: string;
  error?: string;
  metadata: { owl?: string; model?: string; channel?: string; userId?: string };
  createdAt: string;
  updatedAt: string;
  terminatedAt?: string;
}

export interface SessionMessage {
  id: number;
  sessionId: string;
  direction: "to_session" | "from_session";
  content: string;
  createdAt: string;
  consumedAt?: string;
}

export class SessionStore {
  constructor(db: MemoryDatabase) {}

  create(session: Session): void;
  update(id: string, patch: Partial<Session>): void;
  findOne(id: string): Session | null;
  list(filter?: { status?: Session["status"]; parentId?: string; limit?: number }): Session[];

  appendMessage(sessionId: string, direction: SessionMessage["direction"], content: string): SessionMessage;
  pendingMessages(sessionId: string, direction?: SessionMessage["direction"]): SessionMessage[];
  markConsumed(messageId: number): void;
}
```

## `SessionRunner`

```typescript
export interface SessionRunnerOptions {
  pollIntervalMs?: number;     // default 250ms
  maxConcurrent?: number;      // default 5
  defaultTimeoutMs?: number;   // default 30 min
  sessionMaxAgeDays?: number;  // default 7 — older sessions auto-terminate on boot
}

export class SessionRunner {
  constructor(
    store: SessionStore,
    engineFactory: () => OwlEngine,
    baseContext: () => EngineContext,
    opts?: SessionRunnerOptions,
  ) {}

  /** Boot: hydrate non-terminal sessions, auto-terminate ones older than sessionMaxAgeDays. */
  async start(): Promise<void>;

  /** Clean shutdown: persist state, cancel timers. */
  stop(): void;

  /** Returns immediately; runner picks up the session asynchronously. */
  async spawn(opts: { prompt: string; parentId?: string; metadata?: Session["metadata"] }): Promise<Session>;

  /** Insert a message into the queue. Wakes runner if session is awaiting_input. */
  enqueueMessage(sessionId: string, content: string): SessionMessage;

  /** Mark terminated, abort in-flight engine call. Idempotent. */
  terminate(sessionId: string): { terminated: boolean; previousStatus: Session["status"] };

  /** Block until next event (new message / state change) or timeout. */
  awaitNextEvent(sessionId: string, timeoutMs: number): Promise<{
    ready: boolean;
    status: Session["status"];
    newMessages: SessionMessage[];
  }>;
}
```

### Concurrency model

- `maxConcurrent: 5` — at most 5 sessions simultaneously driving an LLM call.
- 6th spawn stays in `pending` state; the runner picks it up FIFO when a slot opens.
- `awaiting_input` sessions don't count against the cap — they're not consuming tokens.

### Hydration on boot

`start()` runs after `memoryDb` and `platform.initialize()`:
1. Auto-terminate sessions older than `sessionMaxAgeDays` (default 7) with `status='terminated'` + `error='auto-terminated: session too old'`.
2. For each non-terminal session: rebuild `EngineContext` from current `baseContext()` factory (live `toolRegistry`), resume engine from persisted `history_json`. The LLM redoes at most one turn (the one that was in-flight when the process died).
3. `awaiting_input` sessions are left untouched — they resume when their next `to_session` message arrives.

### Cancellation

`EngineContext` gains an optional `signal?: AbortSignal`. The engine checks `signal.aborted` at each turn boundary and propagates the signal into the Anthropic SDK call (the SDK natively supports AbortSignal). On terminate, the runner aborts the signal; the engine throws `AbortError`; the runner converts to `status: 'terminated'`.

## Six LLM-callable tools

### `subagents` — spawn

```ts
// Parameters
{
  tasks: string[];                  // 1..N prompts; each spawns one session
  shared_context?: string;          // common preamble prepended to every task
  metadata?: {
    owl?: string;                   // owl persona override (default: parent's owl)
    model?: string;                 // model override (default: parent's model)
  };
}

// Result
{
  spawned: number;
  sessions: Array<{ id: string; prompt: string; status: "pending" }>;
}
```

Returns immediately. Each task spawns one session; runner picks them up asynchronously. Parent agent is free to call other tools or return to the user.

### `sessions_status`

```ts
// Parameters
{ id: string; include_messages?: boolean; since_message_id?: number }

// Result
{
  session: Session;
  messages?: SessionMessage[];
  message_cursor?: number;
}
```

### `sessions_send`

```ts
// Parameters
{ id: string; content: string }

// Result
{ accepted: boolean; queued_message_id: number; current_status: Session["status"] }
```

`accepted: false` when the session is in a terminal state.

### `sessions_yield`

```ts
// Parameters
{ id: string; timeout_ms?: number }    // default 30000, max 600000 (10 min)

// Result
{
  ready: boolean;                      // false if timed out
  status: Session["status"];
  new_messages: SessionMessage[];      // anything from session since last yield/status
}
```

Implemented via polling `pendingMessages` + short-circuit on state change.

### `sessions_list`

```ts
// Parameters
{ status?: Session["status"]; parent_id?: string; limit?: number }   // default limit 50, max 200

// Result
{ sessions: Session[]; total: number }
```

### `sessions_terminate`

```ts
// Parameters
{ id: string }

// Result
{ terminated: boolean; previous_status: Session["status"] }
```

Idempotent on already-terminal sessions.

### Tool category + capabilities

All six register under `category: "cognitive"`. Capabilities:
- `subagents`, `sessions_terminate` → `["session_lifecycle"]`
- `sessions_status`, `sessions_list`, `sessions_yield`, `sessions_send` → `["session_query"]`

No additional permission gates (per the inherit-everything decision).

## Bootstrap wiring

In `src/index.ts`, after `memoryDb` + `platform.initialize()`:

```typescript
const sessionStore = new SessionStore(memoryDb);
const sessionRunner = new SessionRunner(
  sessionStore,
  () => new OwlEngine(),
  () => baseEngineContext,
  { maxConcurrent: 5 },
);
attachSessions(sessionRunner, sessionStore);   // late-bind into the 6 tools
await sessionRunner.start();
process.on("SIGTERM", () => sessionRunner.stop());
process.on("SIGINT",  () => sessionRunner.stop());
```

Same pattern used by `ScheduleRunner` (C2-T17).

## Subagents vs orchestrate_tasks

Both stay. They serve different use cases:

| | `orchestrate_tasks` | `subagents` |
|---|---|---|
| Lifetime | Bounded by parent turn | Outlives parent session |
| Result delivery | Aggregated summary at end | Queryable any time via session id |
| Caller pattern | Fan-out → wait → fan-in | Fire-and-forget; reattach later |
| Use case | "Run 3 things in parallel and give me the combined answer" | "Spawn researchers; check back in an hour" |
| Inter-session messaging | Not supported | Native via `sessions_send`/`sessions_yield` |

Documented in tool descriptions so the LLM picks the right one.

## Cross-cutting

### Observability

- Every lifecycle event logged at `info` with structured fields (sessionId, parentId, status transitions, durationMs, tokens).
- Each session inherits a `traceId` from the spawning context; existing W3C-style trace propagation continues across the spawn boundary.
- A child span `session.run` wraps every engine call inside `SessionRunner`.

### Error handling

- Engine throws → status `failed`, `error` field populated, no retry. Parent sees `failed` on next `sessions_status` and decides.
- Tool errors inside the session don't fail the session — engine handles them at the turn level (as today).
- `sessions_send` to a terminal session returns `{ accepted: false }` — predictable, doesn't throw.
- Database write failures → `log.engine.error` + throw upward. The runner doesn't try to recover from corrupted DB.

### Resource controls

- Per-session: no explicit token budget enforced by runner; engine's existing context management applies. Run-amok sessions can be killed via `sessions_terminate`.
- System-wide: `maxConcurrent: 5` caps simultaneous LLM-driving sessions.
- Cleanup: sessions older than `sessionMaxAgeDays` (default 7) auto-terminate at boot.
- Max session count per user: not enforced in v1.

## Testing strategy

| Subject | Tests | Approach |
|---|---|---|
| `SessionStore` CRUD + queue | 8 | mkdtempSync DB; exercise every method; verify SQL state |
| `SessionRunner` spawn + complete | 4 | stub `OwlEngine` returning canned responses; assert state transitions |
| `SessionRunner` send/yield handshake | 3 | spawn, send mid-run, yield, assert message routed |
| `SessionRunner` terminate mid-run | 2 | spawn long-running stub, terminate, assert clean shutdown via AbortSignal |
| `SessionRunner` hydrate on boot | 3 | persist a `running` session, restart runner, assert resume from history |
| `SessionRunner` concurrency cap | 2 | spawn 7, assert only 5 actively running, 2 pending FIFO |
| 6 tools end-to-end | 6 | each tool's happy path through a real runner+store stack |
| Cross-session message routing | 2 | parent spawns child, child sends back, parent's `sessions_yield` returns the message |

**~30 new tests.** Stub `OwlEngine` avoids real LLM calls. CI runs all of them on ubuntu/macos/windows via the Cycle 3 matrix.

## Risks

| Risk | Mitigation |
|---|---|
| Inherit-everything trust → runaway subagent can do anything the parent can (git push, shell, file edits) | Per explicit user decision. Mitigations in place: every tool call logged (4-point logging); user can audit via `read_logs`; `sessions_terminate` always available. Future cycle can add per-spawn allowlists without breaking the contract. |
| Concurrent SQLite writes from N session runners + main thread → lock contention | `MemoryDatabase` uses `WAL` journal mode (per `CLAUDE.md`); serializes writers, allows concurrent readers. Inserts/updates are millisecond-scale; not a bottleneck at maxConcurrent: 5. |
| Hydration on boot starts old sessions the user no longer cares about | `sessionMaxAgeDays: 7` auto-terminate on boot. User can `sessions_terminate` anything else. |
| Anthropic rate limits at 5 concurrent sessions | `maxConcurrent: 5` is conservative; tunable via constructor. Drop to 3 if rate-limit errors appear in logs. |
| `subagents` overlaps with `orchestrate_tasks` and confuses the LLM | Tool descriptions explicitly distinguish use cases (sync map-reduce vs async background). Different return shapes. |
| Cross-session messaging deadlock — two sessions both `awaiting_input` from each other | `sessions_yield` has hard timeout (max 10 min); both sides time out; LLM sees timeouts and adapts. |
| `OwlEngine.run()` doesn't currently accept a cancellation signal | Adding `AbortSignal` to `EngineContext` is part of this cycle. Engine checks at turn boundaries; Anthropic SDK supports AbortSignal natively for mid-call abort. |
| Hydrated session's `EngineContext` references a stale `toolRegistry` from old process | The runner reconstructs `EngineContext` from the current `baseContext()` factory on every spawn AND every hydration. Always live. |
| `history_json` grows unboundedly for long-running sessions | Engine already implements context compression at session boundaries; sessions inherit that. Hard limit: 7-day auto-terminate caps maximum growth. |

## Deliverables

1. **`src/sessions/`** module (4 files: types, store, runner, engine-host) with full TypeScript types.
2. **6 new tools** under `src/tools/sessions/`, all registered in `src/index.ts`.
3. **2 new SQLite tables** (`sessions`, `session_messages`) added to `MemoryDatabase.createSchema()`.
4. **`AbortSignal` support** added to `EngineContext` (small touch in `src/engine/runtime.ts`).
5. **`attachSessions(runner, store)`** late-bind pattern + bootstrap wiring in `src/index.ts`.
6. **~30 new tests** across the subjects in the testing-strategy table.
7. **Docs**: brief subagents-vs-orchestrate_tasks section appended to `docs/dev-setup.md`.

## Out of scope (future cycles)

- Trust scopes / per-spawn tool allowlists
- Parliament refactor onto subagents
- Cross-process / distributed sessions
- Replay / rewind / re-run
- Web/CLI UI for inspecting sessions (a `/sessions` slash command in the TUI is a reasonable follow-up)
- Per-user session count caps
- Per-session token budget enforcement
