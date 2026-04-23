import chalk from "chalk";
import { AMBER, BLUE, LBL, W, PANEL_BG, SPINNER } from "../shared/palette.js";
import { visLen } from "../shared/text.js";

export interface InputBoxProps {
  buf:     string;
  cursor:  number;
  locked:  boolean;
  masked:  boolean;
  spinIdx: number;
}

export function renderInputBox(props: InputBoxProps, width: number): string {
  const content  = buildContentLine(props);
  const topBorder = PANEL_BG(AMBER("▔".repeat(width + 2)));
  const maxContentW = width - 2;
  const padding = " ".repeat(Math.max(0, maxContentW - visLen(content)));
  const body      = PANEL_BG(" " + content + padding + " ");
  const botBorder = PANEL_BG(AMBER("▁".repeat(width + 2)));
  return topBorder + "\n" + body + "\n" + botBorder;
}

function buildContentLine(props: InputBoxProps): string {
  const { buf, cursor, locked, masked, spinIdx } = props;
  if (locked) {
    return "  " + BLUE(SPINNER[spinIdx % SPINNER.length]) + LBL("  thinking — press ESC to stop");
  }
  const prefix = "  " + AMBER("› ");
  let before: string, atCur: string, after: string;
  if (masked) {
    before = "*".repeat(cursor);
    atCur  = buf[cursor] ? "*" : " ";
    after  = "*".repeat(Math.max(0, buf.length - cursor - 1));
  } else {
    before = buf.slice(0, cursor);
    atCur  = buf[cursor] ?? " ";
    after  = buf.slice(cursor + 1);
  }
  return prefix + W(before) + chalk.bgYellow.black(atCur) + W(after);
}
