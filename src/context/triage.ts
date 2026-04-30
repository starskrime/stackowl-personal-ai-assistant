import type { TriageSignals, ContinuityClass } from "./layer.js";
import { resolveUserId } from "./utils.js";

const FRUSTRATION = /\b(still|again|not working|doesn't work|broken|failed|keeps|why is|why does|wtf)\b/i;
const OPINION = /\b(what do you think|what's your (take|opinion|view)|do you (think|agree|believe)|your thoughts)\b/i;
const TEMPORAL = /\b(last time|yesterday|remember when|before|previously|earlier|last week|back then)\b/i;
const ACTION_KW = /\b(create|build|fix|debug|write|generate|analyze|deploy|install|setup|configure|run|execute)\b/i;

interface TriageInput {
  userMessage: string;
  sessionDepth: number;
  continuityClass: ContinuityClass | null;
  userId?: string;
  sessionId?: string;
  hasActiveItems: boolean;
}

export function computeTriage(input: TriageInput): TriageSignals {
  const { userMessage, sessionDepth, continuityClass, hasActiveItems } = input;
  const isShort = userMessage.trim().length < 80;
  const hasAction = ACTION_KW.test(userMessage);

  return {
    userMessage,
    isConversational: isShort && !hasAction,
    hasFrustration: FRUSTRATION.test(userMessage),
    isOpinionRequest: OPINION.test(userMessage),
    hasTemporalTrigger: TEMPORAL.test(userMessage),
    isReturningUser: continuityClass === "FRESH_START" || continuityClass === "TOPIC_SWITCH",
    sessionDepth,
    hasActiveItems,
    effectiveUserId: resolveUserId(input.userId, input.sessionId),
    continuityClass,
  };
}
