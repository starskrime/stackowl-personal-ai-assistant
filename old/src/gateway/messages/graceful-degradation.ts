import { log } from "../../logger.js";

export interface DegradationContext {
  failedTools: string[];
  userIntent: string;
  owlName: string;
  owlEmoji?: string;
  reason?: string;
}

export function buildDegradationMessage(ctx: DegradationContext): string {
  const { failedTools, userIntent, owlName, owlEmoji = "🦉", reason } = ctx;

  const toolHint = failedTools.length > 0
    ? `I tried ${failedTools.slice(0, 3).map((t) => `\`${t}\``).join(", ")} but couldn't get the information you needed.`
    : "I wasn't able to find the information you needed.";

  const intentSnippet = userIntent.slice(0, 120);

  const suggestionHint = failedTools.includes("web_search") && !failedTools.includes("web_fetch")
    ? " Would you like me to try fetching a specific page directly?"
    : failedTools.includes("web_fetch") && !failedTools.includes("live_browser")
    ? " Would you like me to try a browser-based approach?"
    : "";

  const reasonHint = reason ? ` (${reason.slice(0, 100)})` : "";

  const msg = `${owlEmoji} **${owlName}:** ${toolHint} For "${intentSnippet}"${reasonHint}, I can't produce a useful answer right now.${suggestionHint}`;

  log.gateway.info("pre-delivery-gate.graceful-degradation", {
    owlName,
    failedTools,
    intentLen: userIntent.length,
    hasSuggestion: suggestionHint.length > 0,
  });

  return msg;
}
