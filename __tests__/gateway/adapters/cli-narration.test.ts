import { describe, it, expect, vi, afterEach } from "vitest";
import { GatewayEventBus } from "../../../src/gateway/event-bus.js";

describe("CLI narration wiring", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("wireToolNarration writes narration to stdout on tool:start event", async () => {
    const { wireToolNarration } = await import("../../../src/gateway/adapters/cli.js");
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

  it("wireToolNarration does not write to stdout for tool:goal_advance (silent)", async () => {
    const { wireToolNarration } = await import("../../../src/gateway/adapters/cli.js");
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

  it("wireToolNarration formats narration with prefix ⟳", async () => {
    const { wireToolNarration } = await import("../../../src/gateway/adapters/cli.js");
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
