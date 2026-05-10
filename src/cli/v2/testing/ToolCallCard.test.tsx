import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { ToolCallCard } from "../components/ToolCallCard.js";
import type { ToolCall } from "../state/slices/tools.js";

const baseCall: ToolCall = {
  toolCallId: "tc-1",
  turnId: "turn-1",
  toolName: "bash",
  status: "running",
  startedAt: Date.now(),
  elapsedMs: 0,
};

describe("ToolCallCard", () => {
  it("running state: shows tool name and elapsed time", () => {
    const { lastFrame } = render(
      <ToolCallCard
        tool={{ ...baseCall, status: "running", elapsedMs: 1200 }}
      />,
    );
    expect(lastFrame()).toContain("bash");
    expect(lastFrame()).toContain("1.2s");
  });

  it("running state: shows progress message when present", () => {
    const { lastFrame } = render(
      <ToolCallCard
        tool={{
          ...baseCall,
          status: "running",
          elapsedMs: 0,
          progressMessage: "reading file",
        }}
      />,
    );
    expect(lastFrame()).toContain("reading file");
  });

  it("done state: shows └ connector and ✓ checkmark with time", () => {
    const { lastFrame } = render(
      <ToolCallCard tool={{ ...baseCall, status: "done", elapsedMs: 4100 }} />,
    );
    expect(lastFrame()).toContain("└");
    expect(lastFrame()).toContain("✓");
    expect(lastFrame()).toContain("4.1s");
  });

  it("failed state: shows └ connector, ✗ mark, and error text", () => {
    const { lastFrame } = render(
      <ToolCallCard
        tool={{
          ...baseCall,
          status: "failed",
          elapsedMs: 0,
          error: "permission denied",
        }}
      />,
    );
    expect(lastFrame()).toContain("└");
    expect(lastFrame()).toContain("✗");
    expect(lastFrame()).toContain("permission denied");
  });
});
