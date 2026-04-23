// __tests__/cli/right-panel.test.ts
import { describe, it, expect } from "vitest";
import { renderRightPanel, type RightPanelProps } from "../../src/cli/components/right-panel.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const homeBase: RightPanelProps = { mode: "home", lines: [], scrollOff: 0, recentSessions: [] };
const sessionBase: RightPanelProps = { mode: "session", lines: [], scrollOff: 0, recentSessions: [] };

describe("RightPanel home mode", () => {
  it("shows prompt label", () => {
    const lines = renderRightPanel(homeBase, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("What do you want to work on?"))).toBe(true);
  });
  it("shows recent session titles", () => {
    const props = { ...homeBase, recentSessions: [{ title: "My session", turns: 5, ago: "2h" }] };
    const lines = renderRightPanel(props, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("My session"))).toBe(true);
  });
  it("returns exactly `rows` lines", () => {
    expect(renderRightPanel(homeBase, 60, 20).length).toBe(20);
  });
});

describe("RightPanel session mode", () => {
  it("shows empty prompt when no lines", () => {
    const lines = renderRightPanel(sessionBase, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("What do you want to work on?"))).toBe(true);
  });
  it("shows conversation lines", () => {
    const props = { ...sessionBase, lines: ["  Hello there"] };
    const lines = renderRightPanel(props, 60, 20);
    expect(lines.some(l => l.includes("Hello there"))).toBe(true);
  });
  it("ends with divider line", () => {
    const lines = renderRightPanel(sessionBase, 60, 20);
    expect(stripAnsi(lines[lines.length - 1])).toMatch(/━+/);
  });
});
