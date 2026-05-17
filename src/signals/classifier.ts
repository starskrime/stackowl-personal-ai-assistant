import type { ChatMessage, ChatOptions, ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ContextSignal, SignalSource } from "../ambient/types.js";
import { log } from "../logger.js";

export interface ClassifierProvider {
  chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<{ content: string }>;
}

export interface ClassifierResult {
  keep: boolean;
  confidence: number;
}

const SYSTEM_PROMPT = `You filter ambient workspace signals for relevance to a coding/agent assistant.
Reply JSON only: {"keep": boolean, "confidence": number between 0 and 1}.
- keep=true if the signal is plausibly useful context for a developer right now.
- confidence reflects how confident you are this signal is worth surfacing.
- Mundane signals (e.g. routine clipboard noise, minor time updates) → keep=false.`;

// Sources that produce so much noise their signals skip classification by default
const ALWAYS_SKIP_SOURCES = new Set<SignalSource>(["time_of_day", "system"]);

// Minimum content length to be worth classifying
const MIN_CONTENT_LENGTH = 5;

/**
 * Fast heuristic pre-filter — runs before any LLM call.
 * Returns a definitive result for obvious cases.
 * Returns null when the signal is genuinely ambiguous and needs LLM.
 */
function heuristicClassify(signal: ContextSignal): ClassifierResult | null {
  // Critical signals always pass through
  if (signal.priority === "critical") return { keep: true, confidence: 1.0 };

  // Empty or trivially short content is noise
  const content = signal.content.trim();
  if (content.length < MIN_CONTENT_LENGTH) return { keep: false, confidence: 1.0 };

  // Known noisy sources — skip LLM entirely
  if (ALWAYS_SKIP_SOURCES.has(signal.source)) return { keep: false, confidence: 0.9 };

  // Pure timestamp content is noise (ISO8601 pattern)
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(content) && content.length < 30) {
    return { keep: false, confidence: 0.95 };
  }

  // Signals with meaningful error keywords are almost always relevant
  if (/error|fatal|exception|failed|crash|panic/i.test(content)) {
    return { keep: true, confidence: 0.85 };
  }

  // Ambiguous — let LLM decide
  return null;
}

export class SignalClassifier {
  constructor(private readonly provider: ClassifierProvider) {}

  static create(
    router: IntelligenceRouter,
    providers: Map<string, ModelProvider>,
  ): SignalClassifier {
    const resolved = router.resolve("classification");
    const provider = providers.get(resolved.provider);
    if (!provider) {
      return new SignalClassifier({
        chat: async () => ({ content: `{"keep":false,"confidence":0}` }),
      });
    }
    return new SignalClassifier({
      chat: (messages, _model, options) =>
        provider.chat(messages, resolved.model, options),
    });
  }

  async classify(signal: ContextSignal): Promise<ClassifierResult> {
    // Fast path: heuristic decides without any LLM call
    const heuristic = heuristicClassify(signal);
    if (heuristic !== null) return heuristic;

    // Slow path: LLM for genuinely ambiguous signals
    const userMsg = `source: ${signal.source}\ntitle: ${signal.title}\ncontent: ${signal.content.slice(0, 500)}`;
    try {
      const { content } = await this.provider.chat([
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userMsg },
      ]);
      const parsed = JSON.parse(content);
      const keep = parsed.keep === true;
      const conf = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));
      return { keep, confidence: conf };
    } catch (err) {
      log.engine.warn("[SignalClassifier] LLM classification failed, defaulting to keep=false", { err: String(err) });
      return { keep: false, confidence: 0 };
    }
  }
}
