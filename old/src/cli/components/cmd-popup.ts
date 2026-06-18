import chalk from "chalk";
import { AMBER, W } from "../shared/palette.js";
import { visLen } from "../shared/text.js";

export interface CmdPopupProps {
  matches:     string[];
  selectedIdx: number;
}

const POPUP_BG = chalk.bgRgb(28, 28, 52);

export function renderCmdPopup(props: CmdPopupProps, width: number): string[] {
  const { matches, selectedIdx } = props;
  if (matches.length === 0) return [];

  const visible = matches.slice(0, 8);
  const itemW   = width - 3;
  const lines: string[] = [];

  for (let i = 0; i < visible.length; i++) {
    const cmd = visible[i];
    const pad = " ".repeat(Math.max(0, itemW - visLen(" " + cmd + " ")));
    if (i === selectedIdx) {
      lines.push(AMBER("▌") + chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" " + cmd + " " + pad));
    } else {
      lines.push(AMBER("▌") + POPUP_BG(W(" " + cmd + " " + pad)));
    }
  }
  lines.push(POPUP_BG(AMBER("▁".repeat(width - 1))));
  return lines;
}
