/**
 * StackOwl — Agent Watch: Answer Parser
 *
 * Parses user Telegram replies into structured decisions.
 *
 * Supported formats:
 *   yes abc12           → allow question abc12
 *   no abc12            → deny question abc12
 *   yes all Bash        → allow ALL Bash for this session
 *   no all Write        → deny ALL Write for this session
 *   y / n               → if exactly one pending question for the user
 */

import type { Decision } from "./adapters/base.js";

export type ParsedAnswer =
  | { type: "single"; questionId: string; decision: Decision }
  | { type: "session_all"; toolName: string; decision: Decision }
  | { type: "ambiguous"; decision: Decision }
  | { type: "unknown" };

// Channels API uses 5-letter IDs from [a-km-z], hooks use our own 4-char alphanumeric IDs
const SINGLE_RE = /^\s*(y(?:es)?|n(?:o)?)\s+([a-zA-Z0-9]{4,5})\s*$/i;
const ALL_RE = /^\s*(y(?:es)?|n(?:o)?)\s+all\s+(\S+)\s*$/i;
const BARE_RE = /^\s*(y(?:es)?|n(?:o)?)\s*$/i;

export function parseAnswer(text: string): ParsedAnswer {
  const t = text.trim();

  // "yes abc12" / "no abc12"
  const singleMatch = SINGLE_RE.exec(t);
  if (singleMatch) {
    return {
      type: "single",
      questionId: singleMatch[2].toLowerCase(),
      decision: isYes(singleMatch[1]) ? "allow" : "deny",
    };
  }

  // "yes all Bash" / "no all Write"
  const allMatch = ALL_RE.exec(t);
  if (allMatch) {
    return {
      type: "session_all",
      toolName: allMatch[2],
      decision: isYes(allMatch[1]) ? "allow" : "deny",
    };
  }

  // bare "y" / "yes" / "n" / "no"
  const bareMatch = BARE_RE.exec(t);
  if (bareMatch) {
    return {
      type: "ambiguous",
      decision: isYes(bareMatch[1]) ? "allow" : "deny",
    };
  }

  return { type: "unknown" };
}

function isYes(s: string): boolean {
  return s.toLowerCase().startsWith("y");
}
