import type { EngineResponse } from "../engine/runtime.js";
import { FalseDoneDetector } from "../verification/false-done-detector.js";
import { buildDegradationMessage } from "./messages/graceful-degradation.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export interface PreDeliveryGateOptions {
  provider: ModelProvider;
  userIntent: string;
  owlName: string;
  owlEmoji?: string;
  sessionId: string;
  correctionRun: (correctionPrompt: string) => Promise<EngineResponse>;
}

function isEmptyResponse(content: string): boolean {
  return content.replace(/\[DONE\]/g, "").trim().length === 0;
}

export async function runPreDeliveryGate(
  response: EngineResponse,
  opts: PreDeliveryGateOptions,
): Promise<EngineResponse> {
  if (!isEmptyResponse(response.content)) {
    return response;
  }

  log.gateway.warn("pre-delivery-gate.empty-response-detected", {
    sessionId: opts.sessionId,
    toolsUsed: response.toolsUsed,
    contentLen: response.content.length,
  });

  const detector = new FalseDoneDetector(opts.provider);
  let reason: string | undefined;

  try {
    const verdict = await detector.detect(
      opts.sessionId,
      response.content,
      opts.userIntent,
      opts.provider,
    );
    reason = verdict.reason;
    log.gateway.info("pre-delivery-gate.false-done-verdict", {
      sessionId: opts.sessionId,
      isFalseDone: verdict.isFalseDone,
      reason: verdict.reason,
    });
  } catch (err) {
    log.gateway.warn("pre-delivery-gate.detector-failed", err, { sessionId: opts.sessionId });
  }

  const correctionPrompt = reason
    ? `[CORRECTION NEEDED] Your previous response was empty or had no user-visible content. Reason: ${reason}. Please produce a real answer or explicitly tell the user you cannot help and why.`
    : `[CORRECTION NEEDED] Your previous response was empty. Please produce a real answer or explicitly tell the user you cannot help and why.`;

  try {
    log.gateway.info("pre-delivery-gate.correction-attempt", { sessionId: opts.sessionId });
    const corrected = await opts.correctionRun(correctionPrompt);
    if (!isEmptyResponse(corrected.content)) {
      log.gateway.info("pre-delivery-gate.correction-succeeded", { sessionId: opts.sessionId });
      return corrected;
    }
    log.gateway.warn("pre-delivery-gate.correction-also-empty", { sessionId: opts.sessionId });
  } catch (err) {
    log.gateway.warn("pre-delivery-gate.correction-run-failed", err, { sessionId: opts.sessionId });
  }

  // Strike 2: graceful degradation
  const degradationMsg = buildDegradationMessage({
    failedTools: response.toolsUsed ?? [],
    userIntent: opts.userIntent,
    owlName: opts.owlName,
    owlEmoji: opts.owlEmoji,
    reason,
  });

  return { ...response, content: degradationMsg };
}
