import { describe, it, expect, vi, afterEach, beforeAll } from "vitest";
import { GatewayEventBus } from "../../../src/gateway/event-bus.js";
import type { wireToolNarration as WireToolNarration } from "../../../src/gateway/adapters/cli-v1.js";

// Import once at suite level — cli-v1 has a heavy module graph and the first
// dynamic import can exceed the per-test timeout when loading cold.
let wireToolNarration: typeof WireToolNarration;
beforeAll(async () => {
  ({ wireToolNarration } = await import("../../../src/gateway/adapters/cli-v1.js"));
});

describe("CLI narration wiring", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("wireToolNarration writes narration to stdout on tool:start event", () => {
    const bus = new GatewayEventBus();
    const writes: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk: any) => {
      writes.push(String(chunk));
      return true;
    });

    wireToolNarration(bus);
    bus.emit({ type: "tool:start", toolName: "web_search", args: { query: "test" }, turnId: "t1" });

    expect(writes.some(w => w.includes("Searching the web"))).toBe(true);
  });

  it("wireToolNarration does not write to stdout for tool:goal_advance (silent)", () => {
    const bus = new GatewayEventBus();
    const writes: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk: any) => {
      writes.push(String(chunk));
      return true;
    });

    wireToolNarration(bus);
    bus.emit({ type: "tool:goal_advance", toolName: "web_fetch", subGoal: "find article", verdict: "ADVANCES" });

    expect(writes.length).toBe(0);
  });

  it("wireToolNarration formats narration with prefix ⟳", () => {
    const bus = new GatewayEventBus();
    const writes: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk: any) => {
      writes.push(String(chunk));
      return true;
    });

    wireToolNarration(bus);
    bus.emit({ type: "tool:start", toolName: "read_file", args: { path: "src/index.ts" }, turnId: "t1" });

    expect(writes.some(w => w.includes("⟳"))).toBe(true);
  });
});
