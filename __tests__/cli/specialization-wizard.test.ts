import { describe, it, expect, beforeEach } from "vitest";
import matter from "gray-matter";
import { SpecializationCreateWizard } from "../../src/cli/specialization-wizard.js";
import type { TerminalRenderer } from "../../src/cli/renderer.js";

class MockUI {
  public lines: string[] = [];
  public masked = false;

  printLines(lines: string[]): void {
    this.lines.push(...lines);
  }
  printInfo(msg: string): void {
    this.lines.push(msg);
  }
  setMasked(_on: boolean): void {
    this.masked = _on;
  }
}

describe("SpecializationCreateWizard", () => {
  let wizard: SpecializationCreateWizard;
  let ui: MockUI;

  beforeEach(() => {
    wizard = new SpecializationCreateWizard();
    ui = new MockUI();
  });

  it("should have correct initial step", () => {
    expect(wizard.getCurrentStep()).toBe("welcome");
  });

  it("should start with welcome message", () => {
    wizard.start(ui as unknown as TerminalRenderer);
    expect(ui.lines.length).toBeGreaterThan(0);
    expect(ui.lines[1]).toContain("Create Specialized Owl");
  });

  it("should advance from welcome to name when pressing Enter with no input", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("name");
  });

  it("should advance from welcome to name when providing input", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("TradingBot", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("name");
  });

  it("should advance from name to role", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    await wizard.step("TradingBot", ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("Stock trading assistant", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("role");
  });

  it("should advance from role to emoji", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    await wizard.step("TradingBot", ui as unknown as TerminalRenderer);
    await wizard.step("Stock trading assistant", ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("📈", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("emoji");
  });

  it("should advance from emoji to challenge_level", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    await wizard.step("TradingBot", ui as unknown as TerminalRenderer);
    await wizard.step("Stock trading assistant", ui as unknown as TerminalRenderer);
    await wizard.step("📈", ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("3", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("challenge_level");
  });

  it("should handle cancel command", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    const done = await wizard.step("cancel", ui as unknown as TerminalRenderer);
    expect(done).toBe(true);
  });

  it("should advance through all steps and complete with yes", async () => {
    wizard.start(ui as unknown as TerminalRenderer);

    const steps = [
      "",                        // welcome -> name (Enter advances)
      "TradingBot",              // name -> role (name="TradingBot")
      "Stock trading assistant", // role -> emoji (role="Stock trading assistant")
      "📈",                     // emoji -> challenge_level (emoji="📈")
      "2",                       // challenge_level -> verbosity (challengeLevel="medium")
      "2",                       // verbosity -> tone (verbosity="balanced")
      "precise",                 // tone -> expertise
      "stocks, trading",         // expertise -> allowed_tools
      "",                        // allowed_tools -> denied_tools (no tools)
      "shell",                   // denied_tools -> capability_constraints
      "no live trading",         // capability_constraints -> model_provider
      "4",                       // model_provider -> model_name (default)
      "",                        // model_name -> model_tokens
      "",                        // model_tokens -> skills
      "",                        // skills -> review
      "yes",                     // review -> done
    ];

    let done = false;
    for (const answer of steps) {
      done = await wizard.step(answer, ui as unknown as TerminalRenderer);
      if (done) break;
    }

    expect(done).toBe(true);
    expect(wizard.getSpec().name).toBe("TradingBot");
    expect(wizard.getSpec().role).toBe("Stock trading assistant");
    expect(wizard.getSpec().emoji).toBe("📈");
    expect(wizard.getSpec().challengeLevel).toBe("medium");
  });

  it("should produce valid specialized_owl.md content", async () => {
    wizard.start(ui as unknown as TerminalRenderer);

    const steps = [
      "TestOwl",
      "Code review specialist",
      "🔧",
      "2",
      "2",
      "1",
      "friendly",
      "testing, qa",
      "",
      "delete",
      "no deletions",
      "4",
      "",
      "",
      "",
      "",
      "yes",
    ];

    let done = false;
    for (const answer of steps) {
      done = await wizard.step(answer, ui as unknown as TerminalRenderer);
      if (done) break;
    }

    expect(done).toBe(true);
    const content = wizard.generateSpecFile();
    expect(content.startsWith("---\n")).toBe(true);
    expect(content).toContain("name:");
    expect(content).toContain("emoji:");
    expect(content).toContain("role:");
    expect(content).toContain("challengeLevel:");
    expect(content).toContain("verbosity:");
    const { data } = matter(content);
    expect(data.name).toBeTruthy();
    expect(data.role).toBeTruthy();
  });
});