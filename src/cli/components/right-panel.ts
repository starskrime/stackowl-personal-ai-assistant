// src/cli/components/right-panel.ts
import { LBL, MUT, W } from "../shared/palette.js";
import { trunc, visLen } from "../shared/text.js";

export interface RecentSession { title: string; turns: number; ago: string; }

export interface RightPanelProps {
  mode:            "home" | "session";
  lines:           string[];
  scrollOff:       number;
  recentSessions:  RecentSession[];
}

const DIV = "━";

export function renderRightPanel(props: RightPanelProps, width: number, rows: number): string[] {
  const result:   string[] = [];
  const convRows = rows - 1;

  if (props.mode === "home") {
    const centerRow = Math.floor(rows / 2) - 1;
    for (let i = 0; i < centerRow; i++) result.push("");
    const label    = "What do you want to work on?";
    const labelPad = Math.max(0, Math.floor((width - label.length) / 2));
    result.push(" ".repeat(labelPad) + LBL(label));
    result.push(""); // input box occupies this row

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
  } else {
    if (props.lines.length === 0) {
      result.push("  " + LBL("What do you want to work on?"));
    } else {
      const total = props.lines.length;
      const end   = Math.max(0, total - props.scrollOff);
      const start = Math.max(0, end - convRows);
      const vis   = props.lines.slice(start, end);
      for (let i = 0; i < convRows; i++) result.push(vis[i] ?? "");
    }
  }

  while (result.length < convRows) result.push("");
  result.push("  " + LBL(DIV.repeat(Math.max(0, width - 4))));
  return result.slice(0, rows);
}
