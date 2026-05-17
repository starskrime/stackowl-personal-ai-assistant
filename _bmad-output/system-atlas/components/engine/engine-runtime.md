---
id: engine-runtime
path: src/engine/runtime.ts
subsystem: engine
type: engine
loc: 3256
wired: true
status: in_progress
priority: 1
mapped_week: 5
links:
  imports_from: []
  imported_by: []
---

# src/engine/runtime.ts — OwlEngine

> **Status:** in_progress · **Priority:** P1-critical · **Wiring:** ✅ wired
> **Callers:** 49 · **LoC:** 3256 · **Mermaid ID:** `ER`

## Purpose

Core ReAct loop for StackOwl's AI engine. Receives a conversation context (`EngineContext`) from the gateway, orchestrates streamed provider calls via `withProviderResilience`, runs tool calls in a loop (Think → Act → Observe) until a final response is ready, and returns `EngineResponse`. Also hosts `consumeStream` (streaming event accumulator) and all abort/cancellation machinery.

## Public API

- `OwlEngine` — main class; `execute(context: EngineContext): Promise<EngineResponse>`
- `EngineContext` — input: messages, tools, signal (AbortSignal), owlDna, sessionId, etc.
- `EngineResponse` — output: content, toolCalls, usage, model, finishReason
- `TrajectoryStore` — records per-turn trajectory for evolution
- `EXHAUSTION_MARKER` — sentinel string for max-iterations reached
- `PendingFile`, `PendingCapabilityGap` — intermediate data shapes

## Wiring Status

- **Wired to message path:** yes — called by `GatewayCore` handlers on every user turn
- **Active callers:** 49
- **External subsystem imports:** 26

## Abort / Cancellation Chain (2026-05-17)

The full abort chain from keypress to HTTP cancel:

```
Composer useInput (Escape / Ctrl+C)
  → globalBridge.emit({ kind: "cancel.requested" })
  → adapter.cancelCurrentTurn()
  → AbortController.abort()
  → EngineContext.signal = AbortSignal (aborted)
  → chatOptions.signal = context.signal            [runtime.ts]
  → provider.chatWithToolsStream(…, chatOptions)   [runtime.ts]
  → AnthropicNativeProvider: client.messages.stream(params, { signal })
  → Anthropic SDK HTTP fetch cancelled immediately
  → consumeStream abort listener: stream.return()  [runtime.ts]
  → AbortError thrown from consumeStream
  → withProviderResilience fast-fail (no Layer 2)  [runtime.ts]
  → EngineResponse rejected with AbortError
  → gateway turn marked cancelled
  → UI: turn.cancelled = true
```

### Key functions in the abort path

**`consumeStream(stream, onEvent, signal)`**
- Pre-flight: `if (signal?.aborted) throw new DOMException("Aborted", "AbortError")`
- Abort listener: `signal.addEventListener("abort", () => stream.return(undefined), { once: true })` — fires immediately when signal aborts, closing the async generator before the next token arrives (eliminates 3-6 s TTFT delay)
- Post-loop check: `if (signal?.aborted) throw new DOMException("Aborted", "AbortError")`
- Always cleans up listener in `finally` block

**`withProviderResilience(fn, signal)`** — 3-layer resilience (stream → non-stream → alternate provider)
- Pre-flight at top: `if (signal?.aborted) throw ...` — skips all layers if already cancelled
- AbortError fast-fail in catch: `if (err instanceof DOMException && err.name === "AbortError") throw err` — prevents Layer 2 non-stream fallback from being attempted
- Pre-flight before Layer 2 and Layer 3: `if (signal?.aborted) throw ...`

**`chatOptions`**
- Built inside `execute()` with `signal: context.signal` so the AbortSignal threads through to every provider call.

## Data Flow In/Out

- **In:** `EngineContext` (messages, tools list, owl DNA, AbortSignal, sessionId, traceId)
- **Out:** `EngineResponse` (final text content, accumulated tool calls, token usage, model name)
- Intermediate: streaming `StreamEvent` values from provider (text_delta, tool_start, tool_end, done)

## Open Questions

- `wired: false` in manifest was incorrect — 49 callers confirmed. Updated to `true`.
- LoC 3256 is a strong refactor signal; candidate for extraction per gateway-refactor plan.

## Cross-references

- [[providers-anthropic-native]] — primary provider for streaming calls
- [[providers-manager]] — provider resolution
- [[cli-composer]] — cancellation initiates here
- [[cli-bridge]] — cancel.requested event
- Subsystem rollup: [[engine]]

