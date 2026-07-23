# Channel Adapters → Gateway Ingress Routing

## Sources consulted

- `src/stackowl/channels/base.py` (1-204, full) — `ChannelAdapter` ABC
- `src/stackowl/channels/telegram/adapter.py` L1-1368 — `_mint_request_id` L69-80, `receive()` L320-332, `_handle_update()` L1294-1368
- `src/stackowl/channels/cli_adapter.py` L1-170 — `_next_request_id()` L125-144, `receive()` L154-171
- `src/stackowl/gateway/scanner.py` (full, 386 lines) — `IngressMessage` L53-90, `GatewayScanner.scan()` L303-385
- `src/stackowl/gateway/turn_router.py` (full, 354 lines) — `parse_explicit_signal()` L137-218, `TurnRouter.route()` L256-332
- `src/stackowl/gateway/turn_registry.py` (full, 815 lines)
- `src/stackowl/gateway/inflight_router.py` (full, 236 lines) — `route_inflight_message()` L122-235
- `src/stackowl/startup/orchestrator.py` L90-229, L1420-1499, L1834-2500 — `_dispatch_turn`, `_drain_next`, `_intake`, `_handle_ingress`
- `src/stackowl/pipeline/steps/triage.py` (full, 233 lines)
- `src/stackowl/pipeline/backends/asyncio_backend.py` L95-149

## Concrete findings

**Trace/id minting** happens at the channel adapter (Telegram's `_mint_request_id()`, `uuid4().hex`; CLI's `_next_request_id()`, `f"cli-{session[:8]}-{counter}"`) — but `TraceContext.start(...)` (the contextvar that actually back-propagates into every log line) is called LATER, inside the pipeline backend's `run()` (`asyncio_backend.py:111`), using the already-minted `state.trace_id`. Minting and TraceContext-binding are two separate steps.

**CLAUDE.md discrepancy confirmed**: it says "Every user message mints a UUIDv4 trace_id at the channel adapter via `TraceContext.start(...)`" — the UUID minting is at the adapter, but `TraceContext.start()` itself only runs inside the pipeline backend. Not a bug, a documentation phrasing gap.

**Durability**: `_handle_ingress()` (`orchestrator.py:2442`) writes a `message_ledger_store.insert_pending(...)` row BEFORE the message touches any in-memory structure — this is what makes an inbound message durable at arrival. `TurnRegistry`/`ParkedIntakes` are in-memory only.

**Primary happy path** (Telegram, idle session, plain text):
1. `TelegramChannelAdapter._handle_update()` mints trace_id, builds `IngressMessage`, queues it.
2. `.receive()` pops it off.
3. `_handle_ingress()` inserts the ledger row, then `scanner.scan(msg)`.
4. `GatewayScanner.scan()` — priority: panic → multi-@mention parliament → `@Owl` (exact/fuzzy) → `/command` → (DM only) bare-name vocative → default `RouteDecision(route="owl", target="secretary")`.
5. `pump.resolve_or_rewrite(...)` checks for a pending clarify reply.
6. `_intake(...)` — idle session under capacity → `_dispatch_turn(...)`.
7. `_dispatch_turn` builds `PipelineState(owl_name=decision.target, ...)`, `asyncio.create_task(backend.run(state))`.
8. `turn_registry.register(...)` tracks the in-flight turn; a completion callback drains the next queued intake.
9. Response streams back through the same adapter's `send()`.
10. Inside `backend.run(state)`, the first pipeline step is `triage.run()` — owl routing is finally resolved for the ambiguous "secretary" case.

**Answer to "single decision point or multiple?" — two sequential layers, not duplication:**
1. **`GatewayScanner.scan()`** — fast, deterministic, non-LLM. Only fires for explicit structural signals (`@OwlName`, DM-only bare vocative). Sets `RouteDecision.target`. Everything else falls through to `"secretary"`.
2. **`triage.run()`** (first pipeline step): if `owl_name != "secretary"` (scanner made an explicit decision) → triage only VALIDATES against the registry, never overrides. If `owl_name == "secretary"` (scanner deferred) → triage's FR-9 sticky-cache or `SecretaryRouter` (LLM) makes the actual semantic decision.

Clean precedence chain: scanner.py is authoritative for explicit addressing; triage.py's router is authoritative only when scanner deferred. Not "made and re-made."

3. **`TurnRouter`** is NOT a "which owl" decision at all — it only fires when a turn is already RUNNING for the session, deciding STOP/STEER/NEW. A NEW verdict causes a re-scan reusing `scanner.py`'s own logic, not a third decision engine.

**Error/fallback branches**: unknown `@Owl` → fuzzy match + suggestion; ambiguous vocative → default secretary; global capacity → parked/busy ack; per-session queue full → overflow ack + ledger mark_failed; providers degraded → floors before `backend.run()`; router/veto exceptions fail-safe to NEW.

## Mermaid

```mermaid
flowchart TD
    A["TelegramChannelAdapter._handle_update\nchannels/telegram/adapter.py:1294"] --> B["_mint_request_id (uuid4)\nchannels/telegram/adapter.py:69"]
    B --> C["IngressMessage(chat_id, is_direct, is_reply)\nchannels/telegram/adapter.py:1352"]
    C --> D["asyncio.Queue.put_nowait\nchannels/telegram/adapter.py:1366"]
    D --> E["TelegramChannelAdapter.receive\nchannels/telegram/adapter.py:320"]

    E --> F["_handle_ingress\nstartup/orchestrator.py:2442"]
    F --> G["message_ledger_store.insert_pending\nstartup/orchestrator.py:2461 (DB write)"]
    G --> H["GatewayScanner.scan\ngateway/scanner.py:303"]
    H -->|"panic"| H1["RouteDecision(route=panic)"]
    H -->|"2+ @mentions"| H2["RouteDecision(route=parliament)"]
    H -->|"@Owl mention"| H3["_resolve_owl (exact/fuzzy)\ngateway/scanner.py:159"]
    H -->|"/command"| H4["RouteDecision(route=command)"]
    H -->|"is_direct + vocative name"| H5["_resolve_vocative\ngateway/scanner.py:230"]
    H -->|"default"| H6["RouteDecision(route=owl, target=secretary)\ngateway/scanner.py:385"]

    H1 --> I["pump.resolve_or_rewrite (clarify check)\nstartup/orchestrator.py:2477"]
    H2 --> I
    H3 --> I
    H4 --> I
    H5 --> I
    H6 --> I

    I -->|"consumed by pending clarify"| I1["return — turn resumed elsewhere"]
    I -->|"not consumed"| J["_intake\nstartup/orchestrator.py:2207"]

    J --> K{"turn_registry.running(session_id)?\ngateway/turn_registry.py:249"}
    K -->|"idle + capacity"| L["_dispatch_turn\nstartup/orchestrator.py:1844"]
    K -->|"idle, at global cap"| M["ParkedIntakes.put + enqueue\nack=busy\nstartup/orchestrator.py:2264-2298"]
    K -->|"same-session turn RUNNING"| N["route_inflight_message\ngateway/inflight_router.py:122"]

    N --> O["TurnRouter.route\ngateway/turn_router.py:256"]
    O --> O1["parse_explicit_signal (0-cost)\ngateway/turn_router.py:137"]
    O1 -->|"/stop, bare-stop-token"| P1["TurnRegistry.request_stop\ngateway/turn_registry.py:221 — HANDLED"]
    O1 -->|"/steer, reply-to-inflight"| P2["TurnRegistry.try_steer\ngateway/turn_registry.py:368 — HANDLED"]
    O1 -->|"NONE"| O2["ClarifyIntentClassifier.is_steer (LLM)"]
    O2 -->|"not steer"| P3["ENQUEUE_NEW"]
    O2 -->|"proposed steer"| O3["turn_veto judge"]
    O3 -->|"vetoed"| P3
    O3 -->|"accepted"| P2
    P3 --> Q["re-scan stripped body\nscanner.scan(routed_msg)\nstartup/orchestrator.py:2354"]
    Q --> K

    L --> R["PipelineState(owl_name=decision.target,\nreply_target=msg.chat_id, trace_id=msg.trace_id)\nstartup/orchestrator.py:1913"]
    R --> S["asyncio.create_task(backend.run(state))\nstartup/orchestrator.py:1927"]
    S --> T["TurnRegistry.register(trace_id, task=producer)\ngateway/turn_registry.py:344"]
    T --> U["pump.spawn_send (stream reply)\nstartup/orchestrator.py:1980"]
    T --> V["done-callback → _drain_next\nstartup/orchestrator.py:1968 (FIFO drain on completion)"]

    S -.->|"external: pipeline backend, out of this feature's scope"| W["backend.run(state)"]
    W --> X["TraceContext.start(state.trace_id, ...)\npipeline/backends/asyncio_backend.py:111"]
    X --> Y["triage.run (pipeline step 1)\npipeline/steps/triage.py:32"]
    Y -->|"owl_name != secretary"| Y1["validate against OwlRegistry\n(accept or demote)\npipeline/steps/triage.py:93-129"]
    Y -->|"owl_name == secretary"| Y2["sticky_route_cache hit?\nowls/sticky_route_cache.py"]
    Y2 -->|"yes, conversational, <200 chars"| Y3["reuse cached owl\npipeline/steps/triage.py:148-191"]
    Y2 -->|"no"| Y4["SecretaryRouter.route (LLM)\nowls/router.py:147"]
```

External dependency: `backend.run(state)` is the boundary of this feature.

## Confidence note + known gaps

High confidence — every hop read from source. Did not trace `ClarifyGateway`/`ClarifyPump.resolve_or_rewrite` internals in depth (fallback path, not primary happy path). Did not open Slack/Discord/WhatsApp adapters directly — grep confirmed they funnel through the same `_handle_ingress`/`scanner`/`turn_router`/`turn_registry` machinery at the same call sites in `orchestrator.py`. `GatewayLink`/`SocketTurnClient` (split gateway/core socket-forwarding transport) noted but not expanded — orthogonal to routing logic.
