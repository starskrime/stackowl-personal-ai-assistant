import { describe, it, expect } from "vitest";
import { stripAnsi, visLen, padR, trunc, wrapText } from "../../src/cli/shared/text.js";

describe("stripAnsi", () => {
  it("removes ANSI escape sequences", () => {
    expect(stripAnsi("\x1B[32mhello\x1B[0m")).toBe("hello");
  });
  it("returns plain string unchanged", () => {
    expect(stripAnsi("hello")).toBe("hello");
  });
});

describe("visLen", () => {
  it("measures plain string length", () => {
    expect(visLen("hello")).toBe(5);
  });
  it("ignores ANSI codes", () => {
    expect(visLen("\x1B[32mhi\x1B[0m")).toBe(2);
  });
});

describe("padR", () => {
  it("pads to target width", () => {
    expect(padR("ab", 5)).toBe("ab   ");
  });
  it("does not truncate if already wide", () => {
    expect(padR("abcde", 3)).toBe("abcde");
  });
});

describe("trunc", () => {
  it("truncates long strings with ellipsis", () => {
    expect(trunc("hello world", 7)).toBe("hello w…");
  });
  it("leaves short strings unchanged", () => {
    expect(trunc("hi", 10)).toBe("hi");
  });
});

describe("wrapText", () => {
  it("wraps long lines at word boundaries", () => {
    const result = wrapText("hello world foo", 11);
    expect(result).toEqual(["hello world", "foo"]);
  });
  it("preserves empty lines", () => {
    expect(wrapText("a\n\nb", 80)).toEqual(["a", "", "b"]);
  });
  it("hard-wraps when no space available", () => {
    const result = wrapText("abcdefghij", 5);
    expect(result[0]).toBe("abcde");
  });
});
