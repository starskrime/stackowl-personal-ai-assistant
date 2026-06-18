// __tests__/cli/left-panel.test.ts
import { describe, it, expect } from "vitest";
import { renderLeftPanel, type LeftPanelProps } from "../../src/cli/components/left-panel.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const homeBase: LeftPanelProps = {
  mode: "home", owlState: "idle", spinIdx: 0,
  dna: { challenge: 5, verbosity: 5, mood: 7 }, toolCalls: [],
  instincts: 0, memFacts: 0, skillsHit: 0,
  owlEmoji: "🦉", owlName: "Atlas", generation: 3, challenge: 7,
  provider: "anthropic", model: "claude-sonnet-3-5", skills: 4,
};
const sessionBase: LeftPanelProps = { ...homeBase, mode: "session" };

describe("LeftPanel home mode", () => {
  it("shows owl name", () => {
    const lines = renderLeftPanel(homeBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("Atlas"))).toBe(true);
  });
  it("shows provider", () => {
    const lines = renderLeftPanel(homeBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("anthropic"))).toBe(true);
  });
  it("returns exactly `rows` lines", () => {
    expect(renderLeftPanel(homeBase, 40, 20).length).toBe(20);
  });
});

describe("LeftPanel session mode", () => {
  it("shows OWL MIND section", () => {
    const lines = renderLeftPanel(sessionBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("OWL MIND"))).toBe(true);
  });
  it("shows thinking indicator when state is thinking", () => {
    const lines = renderLeftPanel({ ...sessionBase, owlState: "thinking" }, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("thinking"))).toBe(true);
  });
  it("shows tool call names", () => {
    const props = { ...sessionBase, toolCalls: [{ name: "web_fetch", args: "", status: "done" as const, ms: 120 }] };
    const lines = renderLeftPanel(props, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("web_fetch"))).toBe(true);
  });
  it("shows FIREWALL footer", () => {
    const lines = renderLeftPanel(sessionBase, 40, 20);
    expect(stripAnsi(lines[lines.length - 1])).toContain("FIREWALL");
  });
});
