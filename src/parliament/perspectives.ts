/**
 * StackOwl — Parliament Perspectives
 *
 * Maps debate perspective roles to system prompt overlays and owl selection.
 * When a user asks for "multiple perspectives" or "debate this", perspectives
 * provide role-specific framing for each participating owl.
 */

import type { OwlInstance } from '../owls/persona.js';

export type PerspectiveRole =
  | 'mentor'
  | 'devils_advocate'
  | 'pragmatist'
  | 'visionary'
  | 'empath';

export interface PerspectiveOverlay {
  role: PerspectiveRole;
  label: string;
  emoji: string;
  systemPromptPrefix: string;
}

const PERSPECTIVE_DEFINITIONS: Record<PerspectiveRole, Omit<PerspectiveOverlay, 'role'>> = {
  mentor: {
    label: 'The Mentor',
    emoji: '🧙',
    systemPromptPrefix:
      'You are THE MENTOR in this debate. You are wise, encouraging, and see long-term patterns. ' +
      'Draw on the user\'s past experiences and growth. Focus on what they can learn from this decision. ' +
      'Be warm but honest. Ask "what will you remember about this in 5 years?"',
  },
  devils_advocate: {
    label: "The Devil's Advocate",
    emoji: '😈',
    systemPromptPrefix:
      'You are THE DEVIL\'S ADVOCATE in this debate. Your job is to challenge every assumption. ' +
      'Find the weakest points in others\' arguments. Ask uncomfortable questions. ' +
      'Play the contrarian even if you secretly agree. Push until the argument is pressure-tested.',
  },
  pragmatist: {
    label: 'The Pragmatist',
    emoji: '📊',
    systemPromptPrefix:
      'You are THE PRAGMATIST in this debate. Focus on numbers, logistics, constraints, and ROI. ' +
      'Cut through wishful thinking with data and practical reality. ' +
      'What does the math say? What are the hidden costs? What\'s the risk-adjusted outcome?',
  },
  visionary: {
    label: 'The Visionary',
    emoji: '🔮',
    systemPromptPrefix:
      'You are THE VISIONARY in this debate. Think big-picture, long-term, transformative potential. ' +
      'What could this become? What opportunities are others missing? ' +
      'Push for growth and ambition while acknowledging the leap required.',
  },
  empath: {
    label: 'The Empath',
    emoji: '💚',
    systemPromptPrefix:
      'You are THE EMPATH in this debate. Focus on the human side: emotional impact, ' +
      'mental health, relationships, work-life balance, and personal fulfillment. ' +
      'Ask how this decision will FEEL, not just what it will achieve. Check in on wellbeing.',
  },
};

/**
 * Get all available perspective definitions.
 */
export function getAllPerspectives(): PerspectiveOverlay[] {
  return Object.entries(PERSPECTIVE_DEFINITIONS).map(([role, def]) => ({
    role: role as PerspectiveRole,
    ...def,
  }));
}

/**
 * Get a specific perspective overlay.
 */
export function getPerspective(role: PerspectiveRole): PerspectiveOverlay {
  const def = PERSPECTIVE_DEFINITIONS[role];
  return { role, ...def };
}

/**
 * Assign perspective roles to owls. Maps each owl to a perspective
 * based on their personality traits and challenge level.
 */
export function assignPerspectives(
  owls: OwlInstance[],
  requestedRoles?: PerspectiveRole[],
): Map<string, PerspectiveOverlay> {
  const assignments = new Map<string, PerspectiveOverlay>();

  if (requestedRoles && requestedRoles.length > 0) {
    // Assign requested roles in order
    for (let i = 0; i < Math.min(owls.length, requestedRoles.length); i++) {
      assignments.set(owls[i].persona.name, getPerspective(requestedRoles[i]));
    }
    return assignments;
  }

  // Auto-assign based on owl personality traits
  const available = new Set<PerspectiveRole>([
    'mentor', 'devils_advocate', 'pragmatist', 'visionary', 'empath',
  ]);
  const assigned = new Set<string>();

  for (const owl of owls) {
    if (available.size === 0) break;

    let bestRole: PerspectiveRole | null = null;
    const cl = owl.dna.evolvedTraits.challengeLevel;
    const type = owl.persona.type?.toLowerCase() || '';
    const name = owl.persona.name.toLowerCase();

    // Match by personality
    if ((cl === 'relentless' || cl === 'high') && available.has('devils_advocate') && !assigned.has('devils_advocate')) {
      bestRole = 'devils_advocate';
    } else if ((type.includes('architect') || type.includes('engineer')) && available.has('pragmatist') && !assigned.has('pragmatist')) {
      bestRole = 'pragmatist';
    } else if ((type.includes('executive') || name === 'noctua') && available.has('mentor') && !assigned.has('mentor')) {
      bestRole = 'mentor';
    } else if (type.includes('cost') && available.has('pragmatist') && !assigned.has('pragmatist')) {
      bestRole = 'pragmatist';
    } else {
      // Assign first remaining role
      bestRole = [...available].find(r => !assigned.has(r)) || null;
    }

    if (bestRole) {
      assignments.set(owl.persona.name, getPerspective(bestRole));
      assigned.add(bestRole);
      available.delete(bestRole);
    }
  }

  return assignments;
}

/**
 * Build a perspective-enhanced system prompt for an owl during parliament.
 */
export function buildPerspectivePrompt(
  basePrompt: string,
  perspective: PerspectiveOverlay,
): string {
  return (
    `[PARLIAMENT ROLE: ${perspective.label} ${perspective.emoji}]\n` +
    `${perspective.systemPromptPrefix}\n\n` +
    `Your regular expertise still applies, but filter everything through your role as ${perspective.label}.\n\n` +
    basePrompt
  );
}
