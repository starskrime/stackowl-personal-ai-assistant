/**
 * StackOwl — Runbook Miner
 *
 * Extracts debugging patterns from conversation sessions
 * and converts them into reusable workflow definitions.
 *
 * Zero LLM cost for detection. LLM used only for workflow synthesis.
 */

import type { ChatMessage, ModelProvider } from "../providers/base.js";
import type { WorkflowChainStore } from "./chain.js";
import type { WorkflowDefinition, WorkflowStep } from "./types.js";
import { log } from "../logger.js";

// ─── Detection Patterns ─────────────────────────────────────

const DEBUG_INDICATORS = [
  /\b(?:debug|troubleshoot|diagnose|investigate|fix|resolve)\b/i,
  /\b(?:error|issue|bug|problem|broken|failing|crash)\b/i,
  /\b(?:check logs?|tail|grep|status)\b/i,
  /\b(?:restart|redeploy|rollback|revert)\b/i,
  /\b(?:ssh|kubectl|docker)\b/i,
];

const TOOL_CALL_PATTERN = /tool[_\s]?(?:call|use|execute)[:\s]+["']?(\w+)/gi;

interface DebugSession {
  messages: ChatMessage[];
  toolsUsed: string[];
  startIndex: number;
  endIndex: number;
  topic: string;
}

export class RunbookMiner {
  private minSessionLength = 4; // minimum messages to consider as a runbook

  constructor(
    private chainStore: WorkflowChainStore,
    private provider: ModelProvider,
  ) {}

  /**
   * Scan a conversation for debugging patterns.
   * Returns detected debug sessions (zero LLM cost).
   */
  detectDebugSessions(messages: ChatMessage[]): DebugSession[] {
    const sessions: DebugSession[] = [];
    let currentSession: DebugSession | null = null;
    let gapCount = 0;

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (typeof msg.content !== "string") continue;

      const isDebugRelated = DEBUG_INDICATORS.some((p) =>
        p.test(msg.content as string),
      );

      if (isDebugRelated) {
        if (!currentSession) {
          currentSession = {
            messages: [],
            toolsUsed: [],
            startIndex: i,
            endIndex: i,
            topic: "",
          };
        }
        gapCount = 0;
      } else if (currentSession) {
        gapCount++;
        if (gapCount > 3) {
          // End of debug session
          if (currentSession.messages.length >= this.minSessionLength) {
            currentSession.endIndex = i - gapCount;
            sessions.push(currentSession);
          }
          currentSession = null;
          gapCount = 0;
          continue;
        }
      }

      if (currentSession) {
        currentSession.messages.push(msg);
        currentSession.endIndex = i;

        // Extract tool calls
        if (msg.role === "assistant") {
          const matches = (msg.content as string).matchAll(TOOL_CALL_PATTERN);
          for (const m of matches) {
            if (m[1] && !currentSession.toolsUsed.includes(m[1])) {
              currentSession.toolsUsed.push(m[1]);
            }
          }
        }
      }
    }

    // Close any open session
    if (
      currentSession &&
      currentSession.messages.length >= this.minSessionLength
    ) {
      sessions.push(currentSession);
    }

    return sessions;
  }

  /**
   * Mine a conversation and generate workflow definitions from debug patterns.
   * Uses LLM for synthesis (called sparingly, not every message).
   */
  async mine(messages: ChatMessage[]): Promise<WorkflowDefinition[]> {
    const sessions = this.detectDebugSessions(messages);
    if (sessions.length === 0) return [];

    log.engine.info(
      `[RunbookMiner] Found ${sessions.length} debug session(s), synthesizing workflows`,
    );

    const workflows: WorkflowDefinition[] = [];

    for (const session of sessions) {
      try {
        const workflow = await this.synthesizeWorkflow(session);
        if (workflow) {
          // Check for duplicate
          const existing = this.chainStore.matchTrigger(workflow.triggers[0]);
          if (!existing) {
            await this.chainStore.save(workflow);
            workflows.push(workflow);
            log.engine.info(
              `[RunbookMiner] Created workflow: ${workflow.name}`,
            );
          }
        }
      } catch (err) {
        log.engine.warn(`[RunbookMiner] Failed to synthesize workflow: ${err}`);
      }
    }

    return workflows;
  }

  private async synthesizeWorkflow(
    session: DebugSession,
  ): Promise<WorkflowDefinition | null> {
    const transcript = session.messages
      .map(
        (m) =>
          `${m.role}: ${typeof m.content === "string" ? m.content.slice(0, 300) : "[non-text]"}`,
      )
      .join("\n");

    const prompt = `Analyze this debugging session and extract a reusable runbook workflow.

## Session Transcript
${transcript}

## Tools Used
${session.toolsUsed.join(", ") || "none"}

## Output Format
Return ONLY valid JSON with this structure:
{
  "name": "short descriptive name",
  "description": "what this runbook does",
  "triggers": ["trigger phrase 1", "trigger phrase 2"],
  "tags": ["debugging", "relevant-tags"],
  "steps": [
    {
      "id": "step-1",
      "name": "step description",
      "type": "tool",
      "config": { "toolName": "tool_name", "args": {} }
    }
  ],
  "parameters": [
    { "name": "param_name", "description": "what this param is", "type": "string", "required": true }
  ]
}

If the session is too vague to extract a useful runbook, return {"skip": true}.`;

    const chatResponse = await this.provider.chat([
      { role: "user", content: prompt },
    ]);
    const response = chatResponse.content;

    try {
      // Extract JSON from response
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;

      const parsed = JSON.parse(jsonMatch[0]);
      if (parsed.skip) return null;

      const workflow: WorkflowDefinition = {
        id: `runbook-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        name: parsed.name,
        description: parsed.description,
        triggers: parsed.triggers ?? [],
        parameters: (parsed.parameters ?? []).map(
          (p: Record<string, unknown>) => ({
            name: p.name,
            description: p.description ?? "",
            type: p.type ?? "string",
            required: p.required ?? false,
          }),
        ),
        steps: (parsed.steps ?? []).map((s: Record<string, unknown>) => ({
          id: s.id ?? `step-${Math.random().toString(36).slice(2, 6)}`,
          name: s.name ?? "unnamed",
          type: s.type ?? "tool",
          config: s.config ?? { toolName: "shell", args: {} },
        })) as WorkflowStep[],
        source: "mined",
        tags: parsed.tags ?? ["debugging"],
        createdAt: Date.now(),
        runCount: 0,
      };

      return workflow;
    } catch (err) {
      log.engine.warn(`[RunbookMiner] JSON parse failed: ${err}`);
      return null;
    }
  }
}
