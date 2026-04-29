import { describe, it, expect, vi } from "vitest";
import { InputHandler } from "../../src/cli/input-handler.js";
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeEngine(names: string[], subs: Record<string, string[]> = {}): CompletionEngine {
  return new CompletionEngine({
    topLevelNames: () => names,
    subcommands: (cmd) => subs[cmd] ?? [],
  });
}

describe("InputHandler", () => {
  it("emits line on Enter", () => {
    const h = new InputHandler();
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("h"); h.feed("i"); h.feed("\r");
    expect(onLine).toHaveBeenCalledWith("hi");
  });

  it("does not emit empty line by default", () => {
    const h = new InputHandler();
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("\r");
    expect(onLine).not.toHaveBeenCalled();
  });

  it("emits empty line when allowEmpty=true", () => {
    const h = new InputHandler();
    h.setAllowEmpty(true);
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("\r");
    expect(onLine).toHaveBeenCalledWith("");
  });

  it("handles backspace correctly", () => {
    const h = new InputHandler();
    h.feed("h"); h.feed("i"); h.feed("\x7f");
    expect(h.buf).toBe("h");
    expect(h.cursor).toBe(1);
  });

  it("emits quit on Ctrl+C", () => {
    const h = new InputHandler();
    const onQuit = vi.fn();
    h.on("quit", onQuit);
    h.feed("\x03");
    expect(onQuit).toHaveBeenCalled();
  });

  it("activates cmd popup on /", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help", "status"]));
    h.feed("/");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("filters popup matches as user types", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help", "status", "skills"]));
    h.feed("/"); h.feed("s");
    expect(h.cmdMatches).toEqual(["status", "skills"]);
  });

  it("dismisses popup on ESC", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help"]));
    h.feed("/");
    h.feed("\x1B");
    expect(h.cmdPopupActive).toBe(false);
  });

  it("emits change on each keystroke", () => {
    const h = new InputHandler();
    const onChange = vi.fn();
    h.on("change", onChange);
    h.feed("a");
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("does not accept input when locked", () => {
    const h = new InputHandler();
    h.setLocked(true);
    h.feed("a");
    expect(h.buf).toBe("");
  });

  it("clears buf and emits line after Enter, restoring unmasked state", () => {
    const h = new InputHandler();
    h.setMasked(true);
    h.feed("s"); h.feed("e"); h.feed("\r");
    expect(h.masked).toBe(false);
    expect(h.buf).toBe("");
  });

  // ─── Bug 1 regression: backspace after no-match reopens popup ───

  it("Bug 1: popup reappears after backspace following a no-match filter", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help", "status"]));
    h.feed("/"); h.feed("x"); // /x — no match
    expect(h.cmdPopupActive).toBe(false);
    h.feed("\x7f");           // backspace → buf = "/"
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("Bug 1: popup stays closed when buf no longer starts with /", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help"]));
    h.feed("/"); h.feed("\x7f"); // delete the /
    expect(h.cmdPopupActive).toBe(false);
    expect(h.buf).toBe("");
  });

  // ─── Bug 2 regression: subcommand completion after space ────────

  it("Bug 2: shows subcommands after /skills <space>", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["skills"], { skills: ["list", "install"] }));
    for (const c of "/skills ") h.feed(c);
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["list", "install"]);
  });

  it("Bug 2: filters subcommands by partial", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(
      ["specialization"],
      { specialization: ["list", "show", "create", "delete", "update"] },
    ));
    for (const c of "/specialization s") h.feed(c);
    expect(h.cmdMatches).toEqual(["show"]);
  });

  // ─── Enter applies selected completion ──────────────────────────

  it("Enter applies highlighted command and appends space", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["status", "skills"]));
    h.feed("/"); h.feed("s");                // matches: status, skills; idx=0
    h.feed("\r");                            // select "status"
    expect(h.buf).toBe("/status ");
    expect(h.cmdPopupActive).toBe(false);    // no subcommands for status
  });

  it("Enter on command with subcommands reopens popup with subcommands", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["skills"], { skills: ["list", "install"] }));
    h.feed("/"); h.feed("s");               // matches: skills
    h.feed("\r");                           // select "skills" → buf = "/skills "
    expect(h.buf).toBe("/skills ");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["list", "install"]);
  });
});
