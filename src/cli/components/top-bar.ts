import chalk from "chalk";
import { AMBER, BLUE, GREEN, PURPLE, MUT, LBL, PANEL_BG, DIV } from "../shared/palette.js";
import { padR } from "../shared/text.js";

export interface TopBarProps {
  owlEmoji: string;
  owlName:  string;
  model:    string;
  turn:     number;
  tokens:   number;
  cost:     number;
}

export function renderTopBar(props: TopBarProps, cols: number): string {
  const { owlEmoji, owlName, model, turn, tokens, cost } = props;
  const inner = cols - 2;

  const badge    = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(` ${owlEmoji} ${owlName} `);
  const modelStr = model ? ` ${MUT("[")}${BLUE(model.replace("claude-", "").slice(0, 18))}${MUT("]")}` : "";
  const turnStr  = turn   > 0 ? ` ${MUT("·")} ${PURPLE("turn " + turn)}` : "";
  const toksStr  = tokens > 0 ? ` ${MUT("·")} ${LBL((tokens / 1000).toFixed(1) + "k")}` : "";
  const costStr  = cost   > 0 ? ` ${MUT("·")} ${GREEN("$" + cost.toFixed(3))}` : "";

  const content = badge + modelStr + turnStr + toksStr + costStr;
  const row2 = PANEL_BG("  " + padR(content, inner - 2) + "  ");
  const row3 = PANEL_BG(AMBER(DIV.repeat(cols)));
  return row2 + row3;
}
