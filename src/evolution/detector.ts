/**
 * StackOwl — Capability Gap Detector
 *
 * Identifies when an owl lacks the tools or capabilities
 * to fulfill a user request.
 */

export type GapType = 'TOOL_MISSING' | 'CAPABILITY_GAP';

export interface CapabilityGap {
    type: GapType;
    attemptedToolName?: string;
    userRequest: string;
    description: string;
}

// Structured marker injected by the system prompt — most reliable signal
const STRUCTURED_MARKER = /\[CAPABILITY_GAP:\s*([^\]]+)\]/i;

// Broad fallback patterns for when the marker isn't present
// Ordered from most specific to most general
const GAP_PATTERNS = [
    // Explicit tool mentions
    /i don'?t have (?:a |an |the )?tool/i,
    /no tool (?:for|to|available)/i,
    /would need (?:a |an )?(?:new )?tool/i,

    // Ability / capability
    /i (?:lack|don'?t have) (?:the )?(?:ability|capability)/i,
    /i'?m not (?:able|equipped) to/i,
    /(?:beyond|outside) (?:my|the) (?:current )?capabilities/i,

    // Can't + real-world actions that always require tools
    /(?:can'?t|cannot|unable to) (?:take|capture|record) (?:a |an )?(?:screenshot|screen capture|photo|picture|video)/i,
    /(?:can'?t|cannot|unable to) (?:send|compose|draft) (?:an? )?(?:email|sms|text message|notification)/i,
    /(?:can'?t|cannot|unable to) (?:access|interact with|control) (?:your |the )?(?:screen|desktop|display|gui|browser|app)/i,
    /(?:can'?t|cannot|unable to) (?:make|place) (?:a |an )?(?:phone )?call/i,
    /(?:can'?t|cannot|unable to) (?:play|stream) (?:audio|video|music|sound)/i,
    /(?:can'?t|cannot|unable to) (?:connect to|query|access) (?:your |the )?(?:database|db|api|server)/i,

    // Generic "I can't do X" where X is a real-world action
    /(?:i |unfortunately )?(?:can'?t|cannot|i'?m unable to) (?:capture|view|see|observe|monitor|watch|record|take|open|launch|run|execute) (?:your |the )?(?:screen|desktop|display|application|app|program|browser|window)/i,
];

export class GapDetector {
    /**
     * Check if the LLM's final response signals a capability gap.
     *
     * Checks the structured [CAPABILITY_GAP: ...] marker first (injected via system prompt).
     * Falls back to broad NLP patterns if the marker isn't present.
     */
    detectFromResponse(responseText: string, userRequest: string): CapabilityGap | null {
        // 1. Structured marker — deterministic, no false positives
        const markerMatch = responseText.match(STRUCTURED_MARKER);
        if (markerMatch) {
            return {
                type: 'CAPABILITY_GAP',
                userRequest,
                description: markerMatch[1].trim(),
            };
        }

        // 2. Broad fallback patterns
        for (const pattern of GAP_PATTERNS) {
            if (pattern.test(responseText)) {
                return {
                    type: 'CAPABILITY_GAP',
                    userRequest,
                    description: responseText.slice(0, 300),
                };
            }
        }

        return null;
    }

    /**
     * Build a gap from a tool call that failed because the tool doesn't exist.
     */
    fromMissingTool(toolName: string, userRequest: string): CapabilityGap {
        return {
            type: 'TOOL_MISSING',
            attemptedToolName: toolName,
            userRequest,
            description: `The owl tried to call a tool named "${toolName}" which does not exist in the registry.`,
        };
    }
}
