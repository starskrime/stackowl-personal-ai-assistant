/**
 * StackOwl — Strategy Classifier
 *
 * Single LLM call that replaces both shouldConveneParliament() AND
 * shouldUsePlanner() with a rich execution strategy decision.
 *
 * Returns a TaskStrategy with strategy type, owl assignments, subtasks,
 * and reasoning — all from one model call.
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { TaskStrategy, StrategyType } from './types.js';
import { log } from '../logger.js';

// ─── Quick Exit Patterns ─────────────────────────────────────

const GREETING_PATTERNS = /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|goodbye|good morning|good evening|gm|gn)\b/i;

function isTrivia(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.length < 25) return true;
  if (GREETING_PATTERNS.test(trimmed)) return true;
  return false;
}

// ─── Default Strategy ────────────────────────────────────────

function makeDefault(owlName: string): TaskStrategy {
  return {
    strategy: 'STANDARD',
    reasoning: 'Default strategy',
    confidence: 0.5,
    owlAssignments: [{ owlName, role: 'lead', reasoning: 'Default owl' }],
  };
}

function makeDirect(owlName: string): TaskStrategy {
  return {
    strategy: 'DIRECT',
    reasoning: 'Trivial message, no tools needed',
    confidence: 1.0,
    owlAssignments: [{ owlName, role: 'lead', reasoning: 'Default owl' }],
  };
}

// ─── Owl Summary for Prompt ──────────────────────────────────

function formatOwlsForPrompt(owls: OwlInstance[]): string {
  return owls.map(owl => {
    const specialties = owl.persona.specialties?.join(', ') || 'general';
    const expertise = owl.dna.expertiseGrowth
      ? Object.entries(owl.dna.expertiseGrowth)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 3)
          .map(([k, v]) => `${k}(${v.toFixed(1)})`)
          .join(', ')
      : '';
    return `- ${owl.persona.name} (${owl.persona.type}): specialties=[${specialties}], challenge=${owl.dna.evolvedTraits.challengeLevel}${expertise ? `, expertise=[${expertise}]` : ''}`;
  }).join('\n');
}

// ─── Classify Strategy ───────────────────────────────────────

/**
 * Classify a user message into an execution strategy.
 * Returns a TaskStrategy with strategy type, owl assignments, and optional subtasks.
 */
export async function classifyStrategy(
  userMessage: string,
  owls: OwlInstance[],
  toolNames: string[],
  recentContext: ChatMessage[],
  provider: ModelProvider,
): Promise<TaskStrategy> {
  const defaultOwl = owls.find(o => o.persona.name === 'Noctua')?.persona.name
    ?? owls[0]?.persona.name
    ?? 'Noctua';

  // Quick exit for trivial messages
  if (isTrivia(userMessage)) {
    return makeDirect(defaultOwl);
  }

  const owlSummary = formatOwlsForPrompt(owls);
  const toolSummary = toolNames.slice(0, 20).join(', ');
  const contextSummary = recentContext
    .slice(-3)
    .map(m => `${m.role}: ${(m.content ?? '').slice(0, 150)}`)
    .join('\n');

  const prompt =
    `You are a task routing classifier for an AI assistant with multiple specialist agents (owls). ` +
    `Given a user message, decide the optimal execution strategy.\n\n` +
    `AVAILABLE OWLS:\n${owlSummary}\n\n` +
    `AVAILABLE TOOLS: ${toolSummary}\n\n` +
    (contextSummary ? `RECENT CONVERSATION:\n${contextSummary}\n\n` : '') +
    `STRATEGIES:\n` +
    `- DIRECT: Simple greetings, thanks, trivial questions. No tools. Use default owl (Noctua).\n` +
    `- STANDARD: Most requests. Single owl with tool access. Default when unsure.\n` +
    `- SPECIALIST: Task clearly falls into one owl's domain. Route to that specialist.\n` +
    `- PLANNED: Multi-step work with sequential dependencies. Decompose into subtasks with dependsOn.\n` +
    `- PARLIAMENT: A decision, dilemma, or tradeoff where multiple perspectives genuinely help. Select 2-5 relevant owls.\n` +
    `- SWARM: Multiple INDEPENDENT subtasks that each benefit from a different specialist. Each runs in parallel.\n\n` +
    `RULES:\n` +
    `- Default to STANDARD when unsure.\n` +
    `- SPECIALIST only when there's a clear domain match.\n` +
    `- PARLIAMENT only for genuine dilemmas, NOT factual questions.\n` +
    `- SWARM only when subtasks are truly independent (no data dependencies).\n` +
    `- PLANNED when there are sequential dependencies between steps.\n` +
    `- For PARLIAMENT: assign 2-5 owls based on topic relevance, not always all.\n` +
    `- For SWARM/PLANNED: provide subtasks with id, description, assignedOwl, dependsOn.\n\n` +
    `Respond with ONLY valid JSON:\n` +
    `{\n` +
    `  "strategy": "DIRECT|STANDARD|SPECIALIST|PLANNED|PARLIAMENT|SWARM",\n` +
    `  "reasoning": "one sentence explaining why",\n` +
    `  "confidence": 0.0-1.0,\n` +
    `  "owlAssignments": [{"owlName": "...", "role": "lead|reviewer|subtask:...", "reasoning": "..."}],\n` +
    `  "subtasks": [{"id": 1, "description": "...", "assignedOwl": "...", "dependsOn": [], "toolsNeeded": []}],\n` +
    `  "parliamentConfig": {"topic": "refined debate topic", "owlCount": 3}\n` +
    `}\n\n` +
    `Only include "subtasks" for PLANNED/SWARM. Only include "parliamentConfig" for PARLIAMENT.\n\n` +
    `USER MESSAGE: "${userMessage}"`;

  try {
    const response = await provider.chat(
      [{ role: 'user', content: prompt }],
      undefined,
      { temperature: 0.1, maxTokens: 512 },
    );

    const jsonMatch = response.content.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      log.engine.warn('[Classifier] No JSON in response, defaulting to STANDARD');
      return makeDefault(defaultOwl);
    }

    const parsed = JSON.parse(jsonMatch[0]) as TaskStrategy;

    // Validate strategy type
    const validStrategies: StrategyType[] = ['DIRECT', 'STANDARD', 'SPECIALIST', 'PLANNED', 'PARLIAMENT', 'SWARM'];
    if (!validStrategies.includes(parsed.strategy)) {
      log.engine.warn(`[Classifier] Invalid strategy "${parsed.strategy}", defaulting to STANDARD`);
      return makeDefault(defaultOwl);
    }

    // Ensure at least one owl assignment
    if (!parsed.owlAssignments || parsed.owlAssignments.length === 0) {
      parsed.owlAssignments = [{ owlName: defaultOwl, role: 'lead', reasoning: 'Fallback' }];
    }

    // Validate owl names exist
    const owlNames = new Set(owls.map(o => o.persona.name));
    for (const assignment of parsed.owlAssignments) {
      if (!owlNames.has(assignment.owlName)) {
        // Try case-insensitive match
        const match = owls.find(o =>
          o.persona.name.toLowerCase() === assignment.owlName.toLowerCase(),
        );
        assignment.owlName = match?.persona.name ?? defaultOwl;
      }
    }

    // Validate subtask owl assignments
    if (parsed.subtasks) {
      for (const sub of parsed.subtasks) {
        if (!owlNames.has(sub.assignedOwl)) {
          const match = owls.find(o =>
            o.persona.name.toLowerCase() === sub.assignedOwl.toLowerCase(),
          );
          sub.assignedOwl = match?.persona.name ?? defaultOwl;
        }
      }
    }

    log.engine.info(
      `[Classifier] "${userMessage.slice(0, 60)}..." → ${parsed.strategy} ` +
      `(confidence: ${parsed.confidence?.toFixed(2)}) ` +
      `owls: [${parsed.owlAssignments.map(a => a.owlName).join(', ')}] ` +
      `reason: ${parsed.reasoning}`,
    );

    return parsed;
  } catch (err) {
    log.engine.warn(
      `[Classifier] Failed: ${err instanceof Error ? err.message : String(err)}, defaulting to STANDARD`,
    );
    return makeDefault(defaultOwl);
  }
}
