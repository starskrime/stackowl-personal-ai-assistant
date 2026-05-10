/**
 * StackOwl Observability — Async trace context
 *
 * Uses Node.js AsyncLocalStorage so every async descendant of an originator
 * automatically inherits the trace context without signature changes.
 *
 * The three primitives:
 *   runWithContext(seed, fn) — mint / inherit context at an originator
 *   withSpan(name, fn)       — child span (logs start+end, captures duration)
 *   attachToContext(patch)   — mutate current frame (e.g. after owl/model resolved)
 *   currentTrace()           — read-only accessor used by logger.ts
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { randomSpanId, randomTraceId } from "./ids.js";

export interface TraceContext {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  spanName?: string;
  // Correlation baggage — propagated automatically to all log lines
  sessionId?: string;
  userId?: string;
  channelId?: string;
  messageId?: string;
  owl?: string;
  model?: string;
}

// Module-level ALS instance — one per process.
const _als = new AsyncLocalStorage<TraceContext>();

/**
 * Return the current trace context, or undefined when called outside any
 * runWithContext scope (e.g. during module initialisation).
 */
export function currentTrace(): TraceContext | undefined {
  return _als.getStore();
}

/**
 * Seed or inherit a trace context, then execute fn inside it.
 *
 * - If a parent context exists in the ALS, the new frame inherits its
 *   traceId (unless seed.traceId overrides it).
 * - A fresh spanId is always minted for the new frame.
 * - All baggage fields (sessionId, userId, …) are inherited from the parent
 *   unless the seed explicitly overrides them.
 *
 * This is the ONLY call that creates a new trace root. Use it at:
 *   - Channel adapters (one per inbound message)
 *   - Background loop ticks (CognitiveLoop, ProactivePinger)
 *   - TaskQueue workers re-seeding from a traceparent payload
 */
export function runWithContext<T>(
  seed: Partial<TraceContext>,
  fn: () => T | Promise<T>,
): T | Promise<T> {
  const parent = _als.getStore();
  const ctx: TraceContext = {
    traceId: seed.traceId ?? parent?.traceId ?? randomTraceId(),
    spanId:  seed.spanId  ?? randomSpanId(),
    parentSpanId: seed.parentSpanId ?? parent?.spanId,
    spanName: seed.spanName,
    sessionId: seed.sessionId ?? parent?.sessionId,
    userId:    seed.userId    ?? parent?.userId,
    channelId: seed.channelId ?? parent?.channelId,
    messageId: seed.messageId ?? parent?.messageId,
    owl:   seed.owl   ?? parent?.owl,
    model: seed.model ?? parent?.model,
  };
  return _als.run(ctx, fn);
}

/**
 * Create a child span that wraps an async operation.
 *
 * - Inherits traceId from the current context (throws if none — always call
 *   inside runWithContext).
 * - Mints a new spanId; sets parentSpanId = current spanId.
 * - The logger itself logs span.start / span.end records with durationMs.
 *
 * Usage:
 *   const result = await withSpan("engine.iteration", async () => { ... }, { i });
 */
export async function withSpan<T>(
  spanName: string,
  fn: () => Promise<T>,
  fields?: Record<string, unknown>,
): Promise<T> {
  const parent = _als.getStore();
  const spanId = randomSpanId();
  const ctx: TraceContext = {
    traceId: parent?.traceId ?? randomTraceId(),
    spanId,
    parentSpanId: parent?.spanId,
    spanName,
    sessionId: parent?.sessionId,
    userId:    parent?.userId,
    channelId: parent?.channelId,
    messageId: parent?.messageId,
    owl:   parent?.owl,
    model: parent?.model,
  };

  const start = performance.now();
  return _als.run(ctx, async () => {
    // Lazy import to avoid circular dep at module init time.
    const { _emitSpanStart, _emitSpanEnd } = await import("./logger.js");
    _emitSpanStart(spanName, fields);
    try {
      const result = await fn();
      _emitSpanEnd(spanName, performance.now() - start, false, fields);
      return result;
    } catch (err) {
      _emitSpanEnd(spanName, performance.now() - start, true, fields);
      throw err;
    }
  });
}

/**
 * Mutate the current trace context in-place.
 *
 * Used after routing decisions resolve the owl name or model, so all
 * subsequent log lines in this request carry the correct correlation fields
 * without re-wrapping the entire call tree.
 *
 * Safe to call even when outside a context (no-op).
 */
export function attachToContext(patch: Partial<TraceContext>): void {
  const ctx = _als.getStore();
  if (!ctx) return;
  Object.assign(ctx, patch);
}
