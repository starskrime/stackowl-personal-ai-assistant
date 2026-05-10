import { log } from "../logger.js";

// Per-tool repeat threshold before advisor fires.
// Tools not listed here use DEFAULT_THRESHOLD.
const TOOL_THRESHOLDS: Record<string, number> = {
  web_search:        3,
  smart_search:      3,
  search_web:        3,
  web_fetch:         20,
  smart_fetch:       20,
  live_browser:      20,
  computer_use:      30,
  browser_navigate:  20,
};
const DEFAULT_THRESHOLD = 6;

// What to suggest when a tool hits its threshold.
// Keys should match the tool names in TOOL_THRESHOLDS.
const TOOL_ALTERNATIVES: Record<string, string[]> = {
  web_search:   ["web_fetch (pull the full page)", "live_browser (navigate directly)"],
  smart_search: ["smart_fetch (pull the full page)", "live_browser (navigate directly)"],
  search_web:   ["web_fetch (pull the full page)", "live_browser"],
  web_fetch:    ["live_browser (for JS-rendered content)", "web_search (broaden query)"],
  smart_fetch:  ["live_browser (for JS-rendered content)", "smart_search"],
  live_browser: ["web_fetch (faster for static pages)", "web_search"],
};

export class ToolAdvisor {
  /**
   * Returns the repeat threshold for a given tool name.
   */
  getThreshold(toolName: string): number {
    return TOOL_THRESHOLDS[toolName] ?? DEFAULT_THRESHOLD;
  }

  /**
   * Returns a specific advisory message for an LLM when it has called the
   * same tool too many times without making progress.
   */
  buildAdvisoryMessage(
    toolName: string,
    repeatCount: number,
    userIntent: string,
    fallbacks?: string[],  // from runtime TOOL_FALLBACKS
  ): string {
    const alts = [
      ...(TOOL_ALTERNATIVES[toolName] ?? []),
      ...(fallbacks ?? []).filter((f) => !TOOL_ALTERNATIVES[toolName]?.some((a) => a.startsWith(f))),
    ];

    const altLines = alts.length > 0
      ? alts.map((a) => `  - ${a}`).join("\n")
      : "  - Try a completely different tool or approach";

    const intentSnippet = userIntent.slice(0, 150);

    const msg = [
      `[TOOL ADVISOR: ${toolName}] You have called \`${toolName}\` ${repeatCount} times for this task ("${intentSnippet}…").`,
      `This tool does not appear to be finding what you need. You must switch strategies.`,
      ``,
      `Suggested alternatives:`,
      altLines,
      ``,
      `Choose one of the alternatives above, or if none can help, respond to the user explaining exactly what you tried and what was missing. Do NOT call \`${toolName}\` again.`,
    ].join("\n");

    log.engine.info("tool.advisor.fired", { tool: toolName, repeatCount, altsOffered: alts.length });
    return msg;
  }
}

export const toolAdvisor = new ToolAdvisor();
