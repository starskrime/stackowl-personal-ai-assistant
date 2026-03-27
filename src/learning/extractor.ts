/**
 * StackOwl — Conversation Extractor
 *
 * Analyzes a completed conversation and extracts structured learning signals:
 * - Topics discussed (specific)
 * - Domains involved (broad knowledge areas)
 * - Knowledge gaps (things the assistant couldn't answer or was wrong about)
 * - Unanswered needs (things the user wanted but didn't get)
 * - Research questions (worth studying deeper)
 *
 * Unlike the previous version, this extractor analyzes the FULL conversation
 * including tool results, and explicitly hunts for user needs that went unmet —
 * even when the assistant confidently said "I can't do that."
 */

import type { ChatMessage, ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export interface ConversationInsights {
  /** Specific topics that came up (e.g., "flight tracking", "Telegram bot rate limits") */
  topics: string[];
  /** Broad knowledge domains (e.g., "aviation", "Docker", "finance") */
  domains: string[];
  /** Things the assistant didn't know, was wrong about, or couldn't fulfill */
  knowledgeGaps: string[];
  /** Concrete questions worth researching to improve future answers */
  researchQuestions: string[];
  /** When these insights were extracted */
  timestamp?: string;
}

export class ConversationExtractor {
  constructor(private provider: ModelProvider) {}

  async extract(messages: ChatMessage[]): Promise<ConversationInsights> {
    if (messages.length < 2) {
      return {
        topics: [],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: [],
      };
    }

    // Build a rich transcript that includes tool calls and their results.
    // This lets the analyzer see what tools were attempted, what failed,
    // and what the user actually asked for vs. what they got.
    const transcript = messages
      .slice(-30)
      .map((m) => {
        const role = (m.role as string).toUpperCase();
        const content = (m.content ?? "").slice(0, 500);

        // Include tool call names so the analyzer can see what was attempted
        if (m.toolCalls && m.toolCalls.length > 0) {
          const tools = m.toolCalls
            .map(
              (tc) =>
                `${tc.name}(${JSON.stringify(tc.arguments).slice(0, 100)})`,
            )
            .join(", ");
          return `[${role}]: ${content}\n  [TOOL CALLS: ${tools}]`;
        }

        // For tool results, show the tool name
        if (m.role === "tool" && m.name) {
          const status =
            content.includes("Error") || content.includes("EXIT_CODE: 1")
              ? "❌ FAILED"
              : "✓ OK";
          return `[TOOL:${m.name} ${status}]: ${content.slice(0, 300)}`;
        }

        return `[${role}]: ${content}`;
      })
      .join("\n\n");

    const prompt =
      `You are a learning analyst for an AI assistant. Analyze this conversation transcript ` +
      `and identify what the assistant should learn to be more helpful next time.\n\n` +
      `CONVERSATION:\n${transcript}\n\n` +
      `IMPORTANT: Look for ALL of these signals:\n` +
      `1. USER NEEDS THAT WENT UNMET — The user asked for something specific and didn't get it. ` +
      `   Even if the assistant politely declined ("I can't look up flights"), the underlying ` +
      `   USER NEED is still a gap. Example: user asks "when does flight THY83J arrive?" → ` +
      `   gap: "real-time flight status lookup"\n` +
      `2. WRONG OR INCOMPLETE ANSWERS — The assistant gave incorrect info or missed key details\n` +
      `3. TOOL FAILURES — A tool was called but failed, suggesting the assistant needs ` +
      `   a better approach or different tool\n` +
      `4. TOPICS THE ASSISTANT HEDGED ON — Vague, uncertain, or overly cautious responses ` +
      `   where the user clearly wanted a direct answer\n` +
      `5. REAL-WORLD DATA GAPS — User asked about something that requires current/live data ` +
      `   (prices, weather, flight status, news, stock prices, sports scores) and the ` +
      `   assistant couldn't provide it\n\n` +
      `Return ONLY a JSON object:\n` +
      `{\n` +
      `  "topics": ["specific topics discussed (max 5)"],\n` +
      `  "domains": ["broad knowledge domains (max 4)"],\n` +
      `  "knowledgeGaps": ["specific things the assistant SHOULD have known or been able to do but couldn't (max 4)"],\n` +
      `  "researchQuestions": ["concrete, actionable questions to research so next time the assistant CAN answer (max 4)"]\n` +
      `}\n\n` +
      `Rules:\n` +
      `- Focus on the USER'S actual needs, not just the assistant's self-assessment\n` +
      `- A confident "I can't do that" is STILL a gap — the user wanted it done\n` +
      `- Be specific: "flight status lookup via API" not "improve capabilities"\n` +
      `- Return [] for any list with nothing relevant`;

    try {
      log.evolution.info(
        `[Extractor] Analyzing ${messages.length} messages for learning signals...`,
      );

      const response = await this.provider.chat(
        [
          {
            role: "system",
            content: "You are a learning analyst. Output only valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.1 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```json?/, "")
          .replace(/```$/, "")
          .trim();
      }

      // Extract JSON if embedded in other text
      const jsonMatch = jsonStr.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        jsonStr = jsonMatch[0];
      }

      const parsed = JSON.parse(jsonStr);
      const result = {
        topics: Array.isArray(parsed.topics) ? parsed.topics.slice(0, 5) : [],
        domains: Array.isArray(parsed.domains)
          ? parsed.domains.slice(0, 4)
          : [],
        knowledgeGaps: Array.isArray(parsed.knowledgeGaps)
          ? parsed.knowledgeGaps.slice(0, 4)
          : [],
        researchQuestions: Array.isArray(parsed.researchQuestions)
          ? parsed.researchQuestions.slice(0, 4)
          : [],
      };

      log.evolution.info(
        `[Extractor] Found: ${result.topics.length} topics [${result.topics.join(", ")}], ` +
          `${result.domains.length} domains [${result.domains.join(", ")}], ` +
          `${result.knowledgeGaps.length} gaps [${result.knowledgeGaps.join(", ")}]`,
      );

      return result;
    } catch (err) {
      log.evolution.warn(
        `[Extractor] Failed to extract insights: ${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        topics: [],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: [],
      };
    }
  }
}
