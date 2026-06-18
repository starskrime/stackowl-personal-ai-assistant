/**
 * Integration: trace context propagation across a simulated request pipeline.
 *
 * Pipeline: adapter → gateway → engine → tool
 * Each level uses withSpan; all should share the same traceId.
 */

import { describe, it, expect, beforeEach, afterEach, beforeAll } from "vitest";
import {
  runWithContext,
  withSpan,
  currentTrace,
} from "../../../src/infra/observability/context.js";
import { randomTraceId } from "../../../src/infra/observability/ids.js";
import {
  installTestSink,
  clearTestSink,
  capturedLogs,
} from "../../../src/infra/observability/sinks/test-sink.js";
import { setMinLevel } from "../../../src/infra/observability/logger.js";

// Ensure debug-level records (span.start / span.end) are captured.
beforeAll(() => {
  setMinLevel("debug");
});

// ── Simulated pipeline helpers ──────────────────────────────────────

/** Captures context at its own level, then delegates to the next fn. */
async function simulateTool(
  out: Record<string, string | undefined>,
): Promise<void> {
  out.toolTraceId = currentTrace()?.traceId;
  out.toolSpanId  = currentTrace()?.spanId;
}

async function simulateEngine(
  out: Record<string, string | undefined>,
): Promise<void> {
  out.engineTraceId  = currentTrace()?.traceId;
  out.engineSpanId   = currentTrace()?.spanId;
  await withSpan("tool.exec", async () => simulateTool(out));
  out.toolParentSpanId = out.engineSpanId; // what the tool's parentSpanId should equal
}

async function simulateGateway(
  out: Record<string, string | undefined>,
): Promise<void> {
  out.gatewayTraceId  = currentTrace()?.traceId;
  out.gatewaySpanId   = currentTrace()?.spanId;
  await withSpan("engine.run", async () => simulateEngine(out));
  out.engineParentSpanId = out.gatewaySpanId; // what the engine's parentSpanId should equal
}

// ── Tests ───────────────────────────────────────────────────────────

describe("trace propagation — adapter → gateway → engine → tool", () => {
  beforeEach(() => {
    installTestSink();
  });

  afterEach(() => {
    clearTestSink();
  });

  it("traceId propagates through the full withSpan chain", async () => {
    const seededTraceId = randomTraceId();
    const out: Record<string, string | undefined> = {};

    await runWithContext({ traceId: seededTraceId }, async () => {
      out.adapterTraceId = currentTrace()?.traceId;
      await withSpan("gateway.handle", async () => simulateGateway(out));
    });

    expect(out.adapterTraceId).toBe(seededTraceId);
    expect(out.gatewayTraceId).toBe(seededTraceId);
    expect(out.engineTraceId).toBe(seededTraceId);
    expect(out.toolTraceId).toBe(seededTraceId);
  });

  it("spanId is distinct at every level of the chain", async () => {
    const out: Record<string, string | undefined> = {};

    await runWithContext({}, async () => {
      out.adapterSpanId = currentTrace()?.spanId;
      await withSpan("gateway.handle", async () => {
        out.gatewaySpanId = currentTrace()?.spanId;
        await withSpan("engine.run", async () => {
          out.engineSpanId = currentTrace()?.spanId;
          await withSpan("tool.exec", async () => {
            out.toolSpanId = currentTrace()?.spanId;
          });
        });
      });
    });

    const spanIds = [
      out.adapterSpanId,
      out.gatewaySpanId,
      out.engineSpanId,
      out.toolSpanId,
    ];
    // All must be defined
    spanIds.forEach((id) => expect(id).toBeDefined());
    // All must be unique
    const unique = new Set(spanIds);
    expect(unique.size).toBe(4);
  });

  it("parentSpanId correctly links each level to its parent", async () => {
    const spanIds: Record<string, string | undefined> = {};
    const parentSpanIds: Record<string, string | undefined> = {};

    await runWithContext({}, async () => {
      spanIds.adapter = currentTrace()?.spanId;
      await withSpan("gateway.handle", async () => {
        spanIds.gateway     = currentTrace()?.spanId;
        parentSpanIds.gateway = currentTrace()?.parentSpanId;
        await withSpan("engine.run", async () => {
          spanIds.engine      = currentTrace()?.spanId;
          parentSpanIds.engine  = currentTrace()?.parentSpanId;
          await withSpan("tool.exec", async () => {
            spanIds.tool       = currentTrace()?.spanId;
            parentSpanIds.tool   = currentTrace()?.parentSpanId;
          });
        });
      });
    });

    // Each child's parentSpanId must equal its parent's spanId.
    expect(parentSpanIds.gateway).toBe(spanIds.adapter);
    expect(parentSpanIds.engine).toBe(spanIds.gateway);
    expect(parentSpanIds.tool).toBe(spanIds.engine);
  });

  it("concurrent traces do not bleed into each other", async () => {
    const traceIdA = randomTraceId();
    const traceIdB = randomTraceId();

    const results: Array<{ label: string; traceId: string | undefined }> = [];

    await Promise.all([
      runWithContext({ traceId: traceIdA }, async () => {
        // Yield to the event loop so the other context gets a chance to run.
        await new Promise<void>((r) => setImmediate(r));
        results.push({ label: "A", traceId: currentTrace()?.traceId });
      }),
      runWithContext({ traceId: traceIdB }, async () => {
        await new Promise<void>((r) => setImmediate(r));
        results.push({ label: "B", traceId: currentTrace()?.traceId });
      }),
    ]);

    expect(results).toHaveLength(2);
    const A = results.find((r) => r.label === "A");
    const B = results.find((r) => r.label === "B");
    expect(A?.traceId).toBe(traceIdA);
    expect(B?.traceId).toBe(traceIdB);
  });

  it("withSpan emits span.start and span.end log records", async () => {
    await runWithContext({}, async () => {
      await withSpan("test.operation", async () => {
        // intentionally empty — we only care about the span records
      });
    });

    const logs = capturedLogs();
    const startRecord = logs.find((r) => r.msg.startsWith("span.start: test.operation"));
    const endRecord   = logs.find((r) => r.msg.startsWith("span.end: test.operation"));

    expect(startRecord).toBeDefined();
    expect(endRecord).toBeDefined();
  });
});
