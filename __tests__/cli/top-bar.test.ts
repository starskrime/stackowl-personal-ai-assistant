import { describe, it, expect } from "vitest";
import { renderTopBar, type TopBarProps } from "../../src/cli/components/top-bar.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const base: TopBarProps = { owlEmoji: "🦉", owlName: "Atlas", model: "sonnet-3-5", turn: 0, tokens: 0, cost: 0 };

describe("TopBar", () => {
  it("includes owl name", () => {
    expect(stripAnsi(renderTopBar(base, 100))).toContain("Atlas");
  });
  it("shows turn when turn > 0", () => {
    expect(stripAnsi(renderTopBar({ ...base, turn: 3 }, 100))).toContain("turn 3");
  });
  it("omits turn when turn is 0", () => {
    expect(stripAnsi(renderTopBar(base, 100))).not.toContain("turn");
  });
  it("shows cost when cost > 0", () => {
    expect(stripAnsi(renderTopBar({ ...base, cost: 0.005 }, 100))).toContain("$0.005");
  });
  it("omits cost when cost is 0", () => {
    expect(stripAnsi(renderTopBar(base, 100))).not.toContain("$");
  });
  it("strips claude- prefix from model", () => {
    expect(stripAnsi(renderTopBar({ ...base, model: "claude-sonnet-3-5" }, 100))).toContain("sonnet-3-5");
    expect(stripAnsi(renderTopBar({ ...base, model: "claude-sonnet-3-5" }, 100))).not.toContain("claude-");
  });
});
