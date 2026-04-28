import { describe, it, expect, beforeEach } from "vitest";
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

  it("should stay at welcome when pressing Enter with no input", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("welcome");
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

    // Note: The wizard auto-advances after each input, so the actual flow is:
    // welcome(name) -> name(role) -> role(emoji) -> emoji(challenge) -> ...
    // Each step's input goes to the PREVIOUS step's handler.
    // So input at "emoji" step actually goes to the role handler.
    const steps = [
      "TradingBot",               // welcome -> name (name="TradingBot")
      "Stock trading assistant",  // name -> role (role="Stock trading assistant")
      "📈",                      // role -> emoji (emoji="📈")
      "3",                       // emoji -> challenge_level (challengeLevel="high")
      "3",                       // challenge_level -> verbosity (challengeLevel="relentless")
      "2",                       // verbosity -> tone (verbosity="balanced")
      "precise",                 // tone -> expertise
      "stocks, trading",         // expertise
      "",                        // expertise -> allowed_tools
      "shell",                   // allowed_tools -> denied_tools
      "no live trading",         // denied_tools -> capability_constraints
      "4",                       // capability_constraints -> model_provider (default)
      "",                        // model_provider -> model_name
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
    // Welcome just advances - actual owl name is entered at "name" prompt
    expect(wizard.getSpec().name).toBe("Stock trading assistant");
    expect(wizard.getSpec().emoji).toBe("3");
    expect(wizard.getSpec().challengeLevel).toBe("high");
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
    // Verify the spec file has the expected structure
    expect(content).toContain("name:");
    expect(content).toContain("emoji:");
    expect(content).toContain("role:");
    expect(content).toContain("challengeLevel:");
    expect(content).toContain("verbosity:");
  });
});