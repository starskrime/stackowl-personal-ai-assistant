import chalk from "chalk";
import { LBL, PANEL_BG } from "../shared/palette.js";
import { padR } from "../shared/text.js";

export interface ShortcutEntry { key: string; label: string; }

export function renderShortcutsBar(shortcuts: ShortcutEntry[], cols: number): string {
  const key  = (k: string) => chalk.bgRgb(26, 26, 44).rgb(205, 214, 244).bold(` ${k} `);
  const line = shortcuts.map(s => key(s.key) + LBL("  " + s.label)).join("     ");
  return PANEL_BG(padR(line, cols - 4));
}
