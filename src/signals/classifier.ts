import type { ChatMessage, ChatOptions, ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ContextSignal } from "../ambient/types.js";

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
    } catch {
      return { keep: false, confidence: 0 };
    }
  }
}
