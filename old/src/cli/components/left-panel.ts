import chalk from "chalk";
import { AMBER, BLUE, GREEN, PURPLE, W, LBL, MUT, R, SPINNER } from "../shared/palette.js";
import { trunc } from "../shared/text.js";

export type OwlState = "idle" | "thinking" | "done" | "error";

export interface ToolEntry {
  name:    string;
  args:    string;
  status:  "running" | "done" | "error";
  summary?: string;
  ms?:     number;
}

export interface LeftPanelProps {
  mode:      "home" | "session";
  owlState:  OwlState;
  spinIdx:   number;
  dna:       { challenge: number; verbosity: number; mood: number };
  toolCalls: ToolEntry[];
  instincts: number;
  memFacts:  number;
  skillsHit: number;
  owlEmoji:  string;
  owlName:   string;
  generation: number;
  challenge:  number;
  provider:   string;
  model:      string;
  skills:     number;
}

const OWL_FACES = {
  idle:     " ( o  o ) ",
  thinking: [" ( -_- ) ", " ( o_- ) ", " ( -_o ) ", " ( o_o ) "],
  done:     " ( ^‿^ ) ",
  error:    " ( >_< ) ",
};

export function renderLeftPanel(props: LeftPanelProps, width: number, rows: number): string[] {
  const lines: string[] = [];
  const add   = (t: string) => lines.push(t);
  const blank = () => lines.push("");
  const secHdr = (label: string) =>
    "  " + AMBER.bold(label) + " " + MUT("─".repeat(Math.max(0, width - label.length - 5)));

  if (props.mode === "home") {
    blank();
    add("  " + chalk.bgRgb(250,179,135).rgb(8,8,16).bold(` ${props.owlEmoji} ${props.owlName} `));
    blank();
    add(secHdr("IDENTITY"));
    add("  " + LBL("Generation") + "  " + W(String(props.generation)));
    add("  " + LBL("Challenge ") + "  " + AMBER("⚡" + String(props.challenge)));
    blank();
    add(secHdr("BACKEND"));
    add("  " + LBL("Provider") + "   " + BLUE(props.provider));
    add("  " + LBL("Model   ") + "   " + W(props.model.replace("claude-","").slice(0,14)));
    add("  " + LBL("Skills  ") + "   " + GREEN(String(props.skills) + " loaded"));
  } else {
    blank();
    add(secHdr("OWL MIND"));
    blank();
    add("  " + AMBER(currentFace(props.owlState, props.spinIdx)));
    if (props.owlState === "thinking") {
      add("  " + BLUE(SPINNER[props.spinIdx % SPINNER.length] + " thinking..."));
    }
    blank();
    add("  " + PURPLE("◆") + " " + LBL("Skills   ") + "   " + (props.instincts > 0 ? AMBER.bold(props.instincts + " triggered") : MUT("—")));
    add("  " + PURPLE("◆") + " " + LBL("Memory   ") + "   " + (props.memFacts  > 0 ? AMBER.bold(props.memFacts  + " facts")     : MUT("—")));
    add("  " + PURPLE("◆") + " " + LBL("Skills   ") + "   " + (props.skillsHit > 0 ? GREEN.bold(props.skillsHit + " invoked")   : MUT("—")));
    blank();

    if (props.toolCalls.length > 0) {
      add(secHdr("REASONING"));
      const visible = props.toolCalls.slice(-8);
      visible.forEach((tc, i) => {
        const isLast = i === visible.length - 1;
        const branch = isLast ? MUT("  └ ") : MUT("  ├ ");
        const icon   = tc.status === "running" ? BLUE(SPINNER[props.spinIdx % SPINNER.length])
                     : tc.status === "done"    ? GREEN("✓")
                     : R("✕");
        const name   = tc.status === "running" ? BLUE(trunc(tc.name, width - 18)) : W(trunc(tc.name, width - 18));
        const ms     = tc.ms ? MUT(" " + tc.ms + "ms") : "";
        add(branch + icon + " " + name + ms);
        if (tc.summary) {
          add((isLast ? "        " : "  │     ") + LBL(trunc(tc.summary, width - 12)));
        }
      });
      blank();
    }

    const remaining = rows - lines.length - 7;
    if (remaining > 4) {
      blank();
      add(secHdr("DNA"));
      blank();
      add("  " + dnaBar("challenge", props.dna.challenge, "challenge"));
      add("  " + dnaBar("verbosity", props.dna.verbosity, "verbosity"));
      add("  " + dnaBar("mood     ", props.dna.mood,      "mood"));
    }
  }

  while (lines.length < rows - 1) lines.push("");
  lines.push("  " + MUT("─".repeat(Math.max(0, width - 4))) + " " + MUT("FIREWALL"));
  return lines.slice(0, rows);
}

function currentFace(state: OwlState, spinIdx: number): string {
  if (state === "thinking") return (OWL_FACES.thinking as string[])[Math.floor(spinIdx / 6) % 4];
  if (state === "done")     return OWL_FACES.done;
  if (state === "error")    return OWL_FACES.error;
  return OWL_FACES.idle;
}

function dnaBar(label: string, val: number, trait: "challenge" | "verbosity" | "mood"): string {
  const v     = Math.max(0, Math.min(10, Math.round(val)));
  const color = trait === "challenge" ? AMBER : trait === "verbosity" ? BLUE : GREEN;
  return LBL(label) + " " + color("█").repeat(v) + MUT("█").repeat(10 - v) + " " + MUT(String(val));
}
