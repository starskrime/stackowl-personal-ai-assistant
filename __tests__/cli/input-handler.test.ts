// __tests__/cli/input-handler.test.ts
import { describe, it, expect, vi } from "vitest";
import { InputHandler } from "../../src/cli/input-handler.js";

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
    h.setCommandList(["help", "status"]);
    h.feed("/");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("filters popup matches as user types", () => {
    const h = new InputHandler();
    h.setCommandList(["help", "status", "skills"]);
    h.feed("/"); h.feed("s");
    expect(h.cmdMatches).toEqual(["status", "skills"]);
  });

  it("dismisses popup on ESC", () => {
    const h = new InputHandler();
    h.setCommandList(["help"]);
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
});
