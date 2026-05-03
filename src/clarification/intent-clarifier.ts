/**
 * StackOwl — IntentClarifier
 *
 * LLM-based 4-way intent classifier. Replaces all regex-based clarification
 * logic with a single LLM call that returns both verdict and question text.
 *
 * Fail-open: any parse error or LLM exception returns PROCEED.
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { IntelligenceRouter } from '../intelligence/router.js';
import type { OwlDNA } from '../owls/persona.js';
import type { IntentClassification } from './types.js';
import type { ClarificationCoordinator } from './coordinator.js';
import type { SessionAutonomyBias } from './session-autonomy-bias.js';
import { log } from '../logger.js';

const FAIL_OPEN: IntentClassification = {
  verdict: 'PROCEED',
  question: null,
  interpretation: null,
  reasoning: '',
};

const CLASSIFICATION_PROMPT = `You are classifying a message for a personal AI assistant.

Message: "{message}"
Recent context (last 3 turns): {context}
Owl delegation style: {delegationPreference}
{biasContext}

Classify as one of:
PROCEED — request is clear and actionable, execute immediately
NARRATE — proceed but begin response with: "I'll [interpretation]..."
CLARIFY — genuinely multi-path with no safe default; generate exactly one focused question
USER_CONFUSED — user is expressing their own uncertainty ("not sure which", "I don't know if"); acknowledge and help

Only use CLARIFY if proceeding would execute the WRONG thing.
Brief or informal messages and messages with question words ("where", "what", "how") are NOT ambiguous.

Reply with JSON only:
{"verdict":"PROCEED|NARRATE|CLARIFY|USER_CONFUSED","question":"focused question or null","interpretation":"what you will do or null","reasoning":"one sentence why"}`;

export class IntentClarifier {
  constructor(
    private provider: ModelProvider,
    private router: IntelligenceRouter,
    private coordinator: ClarificationCoordinator,
  ) {}

  async evaluate(
    message: string,
    history: Array<{ role: string; content: string }>,
    dna: OwlDNA,
    bias: SessionAutonomyBias,
    sessionKey = 'default',
  ): Promise<IntentClassification> {
    if (!message.trim()) return FAIL_OPEN;

    try {
      const resolved = this.router.resolve('clarification');
      const contextLines = history
        .slice(-3)
        .map(m => `${m.role}: ${String(m.content).slice(0, 100)}`)
        .join('\n') || '(no prior context)';

      const biasContext = bias.toPromptContext();

      const prompt = CLASSIFICATION_PROMPT
        .replace('{message}', message.slice(0, 400))
        .replace('{context}', contextLines)
        .replace('{delegationPreference}', dna.evolvedTraits.delegationPreference)
        .replace('{biasContext}', biasContext);

      const messages: ChatMessage[] = [{ role: 'user', content: prompt }];

      const response = await this.provider.chat(
        messages,
        resolved.model,
        { temperature: 0.1 },
      );

      const parsed = this.parseResponse(response.content);
      if (!parsed) return FAIL_OPEN;

      if (parsed.verdict === 'CLARIFY' && !parsed.question) {
        parsed.question = `Could you clarify: ${parsed.reasoning}`;
      }

      if (
        (parsed.verdict === 'CLARIFY' || parsed.verdict === 'USER_CONFUSED') &&
        this.coordinator.shouldSuppressDuplicate(parsed.reasoning, sessionKey)
      ) {
        log.engine.info('[IntentClarifier] Duplicate suppressed by coordinator — returning PROCEED');
        return { ...FAIL_OPEN, reasoning: parsed.reasoning };
      }

      return {
        verdict: parsed.verdict as IntentClassification['verdict'],
        question: parsed.question ?? null,
        interpretation: parsed.interpretation ?? null,
        reasoning: parsed.reasoning ?? '',
      };
    } catch (err) {
      log.engine.warn(`[IntentClarifier] Failed — failing open to PROCEED: ${err}`);
      return FAIL_OPEN;
    }
  }

  private parseResponse(content: string): {
    verdict: string;
    question: string | null;
    interpretation: string | null;
    reasoning: string;
  } | null {
    try {
      const match = content.match(/\{[\s\S]*\}/);
      if (!match) return null;
      return JSON.parse(match[0]);
    } catch {
      return null;
    }
  }
}
