// src/cli/components/right-panel.ts
import chalk from "chalk";
import { AMBER, LBL, MUT, W } from "../shared/palette.js";
import { padLeft, trunc, visLen, wrapText } from "../shared/text.js";
import type { ChatMessage } from "../renderer.js";

export interface RecentSession { title: string; turns: number; ago: string; }

export interface RightPanelProps {
  mode:            "home" | "session";
  messages:        ChatMessage[];
  scrollOff:       number;
  recentSessions:  RecentSession[];
}

const DIV = "━";

function renderMessages(messages: ChatMessage[], width: number): string[] {
  const lines: string[] = [];
  const userWrapW   = Math.max(10, Math.min(Math.floor(width * 0.72), width - 4));
  const assistWrapW = Math.max(10, width - 4);

  for (const msg of messages) {
    if (msg.role === "user") {
      // Label: "You" flush right
      lines.push(padLeft(MUT("You"), width - 1));
      // Content: right-aligned, amber
      for (const l of wrapText(msg.content, userWrapW)) {
        lines.push(padLeft(AMBER(l), width - 1));
      }
      lines.push("");
    } else if (msg.role === "assistant") {
      // Label: "  🦉 OwlName:"
      if (msg.label) {
        lines.push("  " + chalk.rgb(205, 214, 244).bold(msg.label) + LBL(":"));
      }
      for (const l of wrapText(msg.content, assistWrapW)) {
        lines.push("  " + W(l));
      }
      lines.push("");
    } else {
      // system: preformatted or plain wrapped
      if (msg.preformatted) {
        for (const l of msg.content.split("\n")) lines.push(l);
      } else {
        for (const l of wrapText(msg.content, assistWrapW)) {
          lines.push("  " + LBL(l));
        }
      }
    }
  }
  return lines;
}

export function renderRightPanel(
  props: RightPanelProps,
  width: number,
  rows: number,
): { lines: string[]; totalLines: number } {
  const result:   string[] = [];
  const convRows = rows - 1;

  if (props.mode === "home") {
    const centerRow = Math.floor(rows / 2) - 1;
    for (let i = 0; i < centerRow; i++) result.push("");
    const label    = "What do you want to work on?";
    const labelPad = Math.max(0, Math.floor((width - label.length) / 2));
    result.push(" ".repeat(labelPad) + LBL(label));
    result.push("");

    const sessions = props.recentSessions.slice(0, 3);
    if (sessions.length > 0) {
      result.push("");
      result.push("  " + MUT("─".repeat(Math.max(0, width - 4))));
      result.push("  " + LBL("recent sessions"));
      result.push("");
      for (const s of sessions) {
        const title   = trunc(s.title, width - 24);
        const turns   = MUT(String(s.turns) + "t");
        const ago     = MUT(s.ago);
        const spacer  = " ".repeat(Math.max(1, width - 2 - visLen(title) - visLen(String(s.turns) + "t") - visLen(s.ago) - 4));
        result.push("  " + W(title) + spacer + turns + "  " + ago);
      }
    }

    while (result.length < convRows) result.push("");
    result.push("  " + LBL(DIV.repeat(Math.max(0, width - 4))));
    return { lines: result.slice(0, rows), totalLines: 0 };
  }

  // Session mode
  if (props.messages.length === 0) {
    result.push("  " + LBL("What do you want to work on?"));
    while (result.length < convRows) result.push("");
    result.push("  " + LBL(DIV.repeat(Math.max(0, width - 4))));
    return { lines: result.slice(0, rows), totalLines: 0 };
  }

  const allLines = renderMessages(props.messages, width);
  const total    = allLines.length;
  const end      = Math.max(0, total - props.scrollOff);
  const start    = Math.max(0, end - convRows);
  const vis      = allLines.slice(start, end);

  for (let i = 0; i < convRows; i++) result.push(vis[i] ?? "");

  while (result.length < convRows) result.push("");
  result.push("  " + LBL(DIV.repeat(Math.max(0, width - 4))));
  return { lines: result.slice(0, rows), totalLines: total };
}
