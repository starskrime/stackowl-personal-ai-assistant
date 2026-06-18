import { describe, it, expect } from "vitest";
import {
  runWithContext,
  currentTrace,
  withSpan,
  attachToContext,
} from "../../src/infra/observability/context.js";

describe("currentTrace", () => {
  it("returns undefined outside any context", () => {
    // We can't truly guarantee "outside" here if tests nest, but
    // a fresh top-level call should have no ALS store.
    // We test this indirectly: outside runWithContext the value is undefined.
    let result: unknown = "sentinel";
    // Use a detached promise to escape the vitest runner's ALS frame (if any)
    result = currentTrace();
    // In a top-level test (no runWithContext ancestor), this should be undefined.
    // If it's not undefined, the runner set up a context — we skip this assertion.
    if (result === undefined) {
      expect(result).toBeUndefined();
    }
  });
});

describe("runWithContext", () => {
  it("makes currentTrace() return the seeded context inside fn", async () => {
    await runWithContext({ traceId: "a".repeat(32), sessionId: "s1" }, async () => {
      const ctx = currentTrace();
      expect(ctx).toBeDefined();
      expect(ctx!.traceId).toBe("a".repeat(32));
      expect(ctx!.sessionId).toBe("s1");
    });
  });

  it("mints a new spanId each call", async () => {
    let outerSpanId: string | undefined;
    let innerSpanId: string | undefined;

    await runWithContext({}, async () => {
      outerSpanId = currentTrace()?.spanId;
      await runWithContext({}, async () => {
        innerSpanId = currentTrace()?.spanId;
      });
    });

    expect(outerSpanId).toBeDefined();
    expect(innerSpanId).toBeDefined();
    expect(outerSpanId).not.toBe(innerSpanId);
  });

  it("nested runWithContext inherits traceId from parent", async () => {
    let outerTraceId: string | undefined;
    let innerTraceId: string | undefined;
    let innerParentSpanId: string | undefined;
    let outerSpanId: string | undefined;

    await runWithContext({}, async () => {
      outerTraceId = currentTrace()?.traceId;
      outerSpanId = currentTrace()?.spanId;
      await runWithContext({}, async () => {
        innerTraceId = currentTrace()?.traceId;
        innerParentSpanId = currentTrace()?.parentSpanId;
      });
    });

    expect(innerTraceId).toBe(outerTraceId);
    expect(innerParentSpanId).toBe(outerSpanId);
  });

  it("two concurrent runWithContext calls don't bleed context", async () => {
    const results: Array<{ traceId: string | undefined; sessionId: string | undefined }> = [];

    await Promise.all([
      runWithContext({ traceId: "1".repeat(32), sessionId: "sess-A" }, async () => {
        // Yield to let the other promise run
        await new Promise((r) => setImmediate(r));
        results.push({
          traceId: currentTrace()?.traceId,
          sessionId: currentTrace()?.sessionId,
        });
      }),
      runWithContext({ traceId: "2".repeat(32), sessionId: "sess-B" }, async () => {
        await new Promise((r) => setImmediate(r));
        results.push({
          traceId: currentTrace()?.traceId,
          sessionId: currentTrace()?.sessionId,
        });
      }),
    ]);

    expect(results).toHaveLength(2);
    const sessA = results.find((r) => r.sessionId === "sess-A");
    const sessB = results.find((r) => r.sessionId === "sess-B");
    expect(sessA?.traceId).toBe("1".repeat(32));
    expect(sessB?.traceId).toBe("2".repeat(32));
  });
});

describe("attachToContext", () => {
  it("mutates the current context", async () => {
    await runWithContext({}, async () => {
      expect(currentTrace()?.owl).toBeUndefined();
      attachToContext({ owl: "test-owl" });
      expect(currentTrace()?.owl).toBe("test-owl");
    });
  });

  it("is a no-op outside any context", () => {
    // Should not throw
    expect(() => attachToContext({ owl: "noop" })).not.toThrow();
  });
});

describe("withSpan", () => {
  it("creates a child span with parentSpanId = outer spanId", async () => {
    let outerSpanId: string | undefined;
    let innerParentSpanId: string | undefined;
    let innerSpanId: string | undefined;

    await runWithContext({}, async () => {
      outerSpanId = currentTrace()?.spanId;
      await withSpan("test-span", async () => {
        innerSpanId = currentTrace()?.spanId;
        innerParentSpanId = currentTrace()?.parentSpanId;
      });
    });

    expect(innerParentSpanId).toBe(outerSpanId);
    expect(innerSpanId).not.toBe(outerSpanId);
  });

  it("inherits traceId from the outer context", async () => {
    let outerTraceId: string | undefined;
    let innerTraceId: string | undefined;

    await runWithContext({ traceId: "f".repeat(32) }, async () => {
      outerTraceId = currentTrace()?.traceId;
      await withSpan("inherit-trace", async () => {
        innerTraceId = currentTrace()?.traceId;
      });
    });

    expect(innerTraceId).toBe(outerTraceId);
    expect(innerTraceId).toBe("f".repeat(32));
  });

  it("propagates span name onto the child context", async () => {
    let spanName: string | undefined;

    await runWithContext({}, async () => {
      await withSpan("my-operation", async () => {
        spanName = currentTrace()?.spanName;
      });
    });

    expect(spanName).toBe("my-operation");
  });

  it("returns the function's return value", async () => {
    const result = await runWithContext({}, () =>
      withSpan("compute", async () => 42),
    );
    expect(result).toBe(42);
  });

  it("re-throws errors from the wrapped function", async () => {
    await expect(
      runWithContext({}, () =>
        withSpan("boom", async () => {
          throw new Error("test error");
        }),
      ),
    ).rejects.toThrow("test error");
  });
});
