import { describe, it, expect } from "vitest";
import { renderCmdPopup, type CmdPopupProps } from "../../src/cli/components/cmd-popup.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

describe("CmdPopup", () => {
  it("returns empty array when no matches", () => {
    expect(renderCmdPopup({ matches: [], selectedIdx: 0 }, 40)).toEqual([]);
  });
  it("renders one line per match plus border", () => {
    const lines = renderCmdPopup({ matches: ["help", "status"], selectedIdx: 0 }, 40);
    expect(lines.length).toBe(3); // 2 items + border
  });
  it("caps at 8 visible items", () => {
    const matches = ["a","b","c","d","e","f","g","h","i","j"];
    const lines = renderCmdPopup({ matches, selectedIdx: 0 }, 40);
    expect(lines.length).toBe(9); // 8 items + border
  });
  it("includes match text in output", () => {
    const lines = renderCmdPopup({ matches: ["help"], selectedIdx: 0 }, 40);
    expect(stripAnsi(lines[0])).toContain("help");
  });
});
