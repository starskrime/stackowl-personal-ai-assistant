/**
 * StackOwl — Proactive Anticipator
 *
 * Uses the holistic user profile from MicroLearner + pattern data to
 * proactively anticipate what the user will need BEFORE they ask.
 *
 * Key behaviors:
 *   - Suggests skill creation for anticipated capabilities
 *   - Pre-generates content for predicted patterns
 *   - Connects preference data with pattern predictions
 *   - Adjusts owl behavior proactively based on profile trends
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { MicroLearner } from './micro-learner.js';
import type { PatternAnalyzer } from '../predictive/analyzer.js';
import type { Skill } from '../skills/types.js';
import { log } from '../logger.js';

export interface Anticipation {
  type: 'skill_suggestion' | 'content_prep' | 'behavior_adjustment' | 'proactive_message';
  capability: string;
  reason: string;
  confidence: number;
  suggestedAction?: string;
  preparedContent?: string;
}

export class ProactiveAnticipator {
  constructor(
    private microLearner: MicroLearner,
    private patternAnalyzer: PatternAnalyzer | null,
    private provider: ModelProvider,
  ) {}

  /**
   * Run a full anticipation cycle. Called periodically (e.g., every 10 messages
   * or on session start). Uses the user profile + patterns to generate
   * proactive suggestions.
   */
  async anticipate(existingSkills: Skill[]): Promise<Anticipation[]> {
    const anticipations: Anticipation[] = [];
    const profile = this.microLearner.getProfile();

    if (profile.totalMessages < 10) {
      return []; // Not enough data yet
    }

    // ─── 1. Skill gap anticipation from capability clusters ───
    const anticipated = this.microLearner.getAnticipatedNeeds();
    const existingSkillNames = new Set(existingSkills.map(s => s.name.toLowerCase()));

    for (const need of anticipated) {
      if (existingSkillNames.has(need.capability)) continue;
      if (need.confidence < 0.4) continue;

      anticipations.push({
        type: 'skill_suggestion',
        capability: need.capability,
        reason: need.reason,
        confidence: need.confidence,
        suggestedAction: `Consider creating a "${need.capability}" skill — the user's usage patterns suggest they'll need it`,
      });
    }

    // ─── 2. Pattern-based content preparation ─────────────────
    if (this.patternAnalyzer) {
      const upcoming = this.patternAnalyzer.getUpcoming(12); // Next 12 hours
      for (const pattern of upcoming) {
        if (pattern.confidence < 0.6) continue;

        anticipations.push({
          type: 'content_prep',
          capability: pattern.action,
          reason: `User typically "${pattern.action}" at this time (${pattern.frequency}x observed)`,
          confidence: pattern.confidence,
          suggestedAction: `Pre-prepare content for: ${pattern.action}`,
        });
      }
    }

    // ─── 3. Behavior adjustments from profile trends ──────────
    const sentimentRatio = profile.positiveSignals / Math.max(1, profile.positiveSignals + profile.negativeSignals);

    if (sentimentRatio < 0.4 && profile.totalMessages > 20) {
      anticipations.push({
        type: 'behavior_adjustment',
        capability: 'response_style',
        reason: `High negative signal rate (${(sentimentRatio * 100).toFixed(0)}% positive) — user may be frustrated with current behavior`,
        confidence: 0.7,
        suggestedAction: 'Reduce verbosity, be more direct, and avoid unsolicited suggestions',
      });
    }

    if (profile.commandRate > 0.7) {
      anticipations.push({
        type: 'behavior_adjustment',
        capability: 'interaction_mode',
        reason: `User sends commands ${(profile.commandRate * 100).toFixed(0)}% of the time — they prefer action over conversation`,
        confidence: 0.8,
        suggestedAction: 'Minimize explanations, execute immediately, confirm only when necessary',
      });
    }

    // ─── 4. LLM-powered deep anticipation (periodic) ─────────
    // Only run this if we have enough data and it's been a while
    if (profile.totalMessages >= 30 && anticipated.length > 0) {
      try {
        const deepAnticipations = await this.deepAnticipate(profile, existingSkillNames);
        anticipations.push(...deepAnticipations);
      } catch (err) {
        log.engine.warn(`[Anticipator] Deep anticipation failed: ${err}`);
      }
    }

    // Deduplicate by capability
    const seen = new Set<string>();
    return anticipations.filter(a => {
      if (seen.has(a.capability)) return false;
      seen.add(a.capability);
      return true;
    });
  }

  /**
   * LLM-powered deep anticipation. Asks the model to think about the
   * user as a person and predict what they'll need.
   */
  private async deepAnticipate(
    profile: import('./micro-learner.js').UserProfile,
    existingSkills: Set<string>,
  ): Promise<Anticipation[]> {
    const topTopics = Object.entries(profile.topics)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([topic, count]) => `${topic} (${count}x)`);

    const topTools = Object.entries(profile.toolUsage)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([tool, count]) => `${tool} (${count}x)`);

    const peakHours = this.microLearner.getPeakHours();

    const prompt = `You are analyzing a user's behavioral profile to predict what they will need next.

USER PROFILE:
- Total messages: ${profile.totalMessages}
- Top topics: ${topTopics.join(', ') || 'none yet'}
- Most used tools: ${topTools.join(', ') || 'none yet'}
- Peak active hours: ${peakHours.map(h => `${h}:00`).join(', ') || 'varies'}
- Communication style: ${profile.commandRate > 0.5 ? 'command-oriented' : profile.questionRate > 0.4 ? 'question-oriented' : 'conversational'}
- Message length: ${profile.avgMessageLength > 100 ? 'detailed' : profile.avgMessageLength > 40 ? 'moderate' : 'brief'}
- Existing skills: ${Array.from(existingSkills).join(', ') || 'none'}

Think about this person holistically. Based on their usage patterns, what capabilities might they need that they haven't asked for yet?

Return a JSON array of predictions:
[{
  "capability": "short_name",
  "reason": "why they'd need this based on their profile",
  "confidence": 0.0-1.0
}]

Only include capabilities NOT in the existing skills list. Max 5 predictions. Output JSON only.`;

    const messages: ChatMessage[] = [
      { role: 'system', content: 'You are a behavioral prediction engine. Output valid JSON only.' },
      { role: 'user', content: prompt },
    ];

    const response = await this.provider.chat(messages, undefined, { temperature: 0.3 });
    const cleaned = response.content.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
    const predictions: Array<{ capability: string; reason: string; confidence: number }> = JSON.parse(cleaned);

    if (!Array.isArray(predictions)) return [];

    return predictions
      .filter(p => p.confidence >= 0.4 && !existingSkills.has(p.capability))
      .map(p => ({
        type: 'skill_suggestion' as const,
        capability: p.capability,
        reason: p.reason,
        confidence: p.confidence,
        suggestedAction: `Consider creating a "${p.capability}" skill`,
      }));
  }

  /**
   * Generate a proactive message for the user based on anticipations.
   * Only called when the system decides to proactively reach out.
   */
  async generateProactiveContent(anticipation: Anticipation): Promise<string | null> {
    if (anticipation.type !== 'content_prep') return null;

    try {
      const messages: ChatMessage[] = [
        {
          role: 'system',
          content: 'You are a proactive AI assistant. Generate useful, concise content the user will find valuable. Be natural and helpful, not robotic.',
        },
        {
          role: 'user',
          content: `The user typically "${anticipation.capability}" around this time. Prepare a brief, useful response. Keep it under 150 words and make it feel personal and helpful.`,
        },
      ];

      const response = await this.provider.chat(messages, undefined, { temperature: 0.5 });
      return response.content;
    } catch {
      return null;
    }
  }
}
