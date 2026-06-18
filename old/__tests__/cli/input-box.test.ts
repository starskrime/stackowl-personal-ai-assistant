import { describe, it, expect } from "vitest";
import { renderInputBox, type InputBoxProps } from "../../src/cli/components/input-box.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const base: InputBoxProps = { buf: "", cursor: 0, locked: false, masked: false, spinIdx: 0 };

describe("InputBox", () => {
  it("shows prompt arrow when unlocked", () => {
    expect(stripAnsi(renderInputBox(base, 60))).toContain("›");
  });
  it("shows thinking message when locked", () => {
    expect(stripAnsi(renderInputBox({ ...base, locked: true }, 60))).toContain("thinking");
  });
  it("shows buffer content", () => {
    expect(stripAnsi(renderInputBox({ ...base, buf: "hello", cursor: 5 }, 60))).toContain("hello");
  });
  it("masks buffer when masked=true", () => {
    const out = stripAnsi(renderInputBox({ ...base, buf: "secret", cursor: 6, masked: true }, 60));
    expect(out).not.toContain("secret");
    expect(out).toContain("*");
  });
  it("returns three lines (top border, content, bottom border)", () => {
    const lines = stripAnsi(renderInputBox(base, 60)).split("\n");
    expect(lines.length).toBe(3);
  });
});
