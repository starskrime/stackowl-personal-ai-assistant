/**
 * StackOwl — Echo Chamber Detector
 *
 * Identifies cognitive biases, unchallenged assumptions, and repetitive patterns
 * in conversation history. Generates calibrated challenges.
 */

import type { ModelProvider } from '../providers/base.js';
import type { SessionStore } from '../memory/store.js';
import type {
  BiasDetection,
  ChallengeIntensity,
  EchoChamberAnalysis,
} from './types.js';
import { join } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync, mkdirSync } from 'node:fs';
import { log } from '../logger.js';

export class EchoChamberDetector {
  private sessionStore: SessionStore;
  private provider: ModelProvider;
  private workspacePath: string;
  private lastAnalysis: EchoChamberAnalysis | null = null;
  private intensity: ChallengeIntensity;

  constructor(
    sessionStore: SessionStore,
    provider: ModelProvider,
    workspacePath: string,
    intensity: ChallengeIntensity = 'balanced',
  ) {
    this.sessionStore = sessionStore;
    this.provider = provider;
    this.workspacePath = workspacePath;
    this.intensity = intensity;
  }

  /**
   * Run a full echo chamber analysis on recent conversation history.
   */
  async analyze(minSessions: number = 5): Promise<EchoChamberAnalysis> {
    const sessions = await this.sessionStore.listSessions();
    const recentSessions = sessions.slice(0, 20);

    if (recentSessions.length < minSessions) {
      return {
        detections: [],
        overallAssessment: `Not enough conversation history yet (${recentSessions.length}/${minSessions} sessions).`,
        analyzedAt: new Date().toISOString(),
        sessionCount: recentSessions.length,
      };
    }

    // Step 1: Heuristic pre-filter
    const heuristics = this.runHeuristics(recentSessions);

    // Step 2: LLM deep analysis (only if heuristics found something)
    let detections: BiasDetection[] = [];
    let overallAssessment = '';

    if (heuristics.hasSignals) {
      const llmResult = await this.runLLMAnalysis(recentSessions, heuristics);
      detections = llmResult.detections;
      overallAssessment = llmResult.assessment;
    } else {
      overallAssessment = 'No significant bias patterns detected in recent conversations. Your thinking appears balanced.';
    }

    const analysis: EchoChamberAnalysis = {
      detections,
      overallAssessment,
      analyzedAt: new Date().toISOString(),
      sessionCount: recentSessions.length,
    };

    this.lastAnalysis = analysis;
    await this.persist(analysis);

    return analysis;
  }

  /**
   * Get cached detections for context injection.
   */
  getRecentDetections(): BiasDetection[] {
    return this.lastAnalysis?.detections ?? [];
  }

  /**
   * Get the last analysis result.
   */
  getLastAnalysis(): EchoChamberAnalysis | null {
    return this.lastAnalysis;
  }

  /**
   * Generate a challenge message for the user.
   */
  async generateChallenge(intensity?: ChallengeIntensity): Promise<string | null> {
    const detections = this.getRecentDetections();
    if (detections.length === 0) return null;

    const level = intensity || this.intensity;
    const topDetection = detections.sort((a, b) => b.confidence - a.confidence)[0];

    const toneGuide: Record<ChallengeIntensity, string> = {
      gentle: 'Be warm and supportive. Frame observations as curious questions, not accusations. Use "I noticed" and "I wonder if".',
      balanced: 'Be direct but respectful. State observations clearly and ask probing questions. Balance honesty with empathy.',
      relentless: 'Be blunt and unsparing. Challenge every assumption. Don\'t sugarcoat. Push hard for self-reflection. Channel Socrates at his most relentless.',
    };

    try {
      const response = await this.provider.chat(
        [
          {
            role: 'user',
            content:
              `Generate a challenge message for a user who shows signs of ${topDetection.bias.replace(/_/g, ' ')}.\n\n` +
              `Evidence: ${topDetection.evidence}\n\n` +
              `Tone: ${toneGuide[level]}\n\n` +
              `Write 2-4 sentences that challenge this pattern. Be specific to their behavior. ` +
              `End with a thought-provoking question.`,
          },
        ],
        undefined,
        { temperature: 0.5, maxTokens: 250 },
      );

      return response.content.trim();
    } catch (err) {
      log.engine.debug(`[EchoChamber] Challenge generation failed: ${err}`);
      return topDetection.suggestedChallenge;
    }
  }

  /**
   * Format detections as context for injection into system prompt.
   */
  toContextString(): string {
    const detections = this.getRecentDetections();
    if (detections.length === 0) return '';

    const lines = detections
      .filter(d => d.confidence >= 0.5)
      .slice(0, 3)
      .map(d => `- ${d.bias.replace(/_/g, ' ')}: ${d.evidence} (confidence: ${(d.confidence * 100).toFixed(0)}%)`);

    if (lines.length === 0) return '';

    return (
      '\n<echo_chamber_awareness>\n' +
      'Recent bias patterns detected in this user\'s conversations:\n' +
      lines.join('\n') + '\n' +
      'When relevant, gently challenge these patterns. Don\'t mention this system by name.\n' +
      '</echo_chamber_awareness>\n'
    );
  }

  // ─── Private ─────────────────────────────────────────────

  private runHeuristics(sessions: Array<{ id: string; messages: Array<{ role: string; content: string }> }>): {
    hasSignals: boolean;
    topicRepetitions: Map<string, number>;
    agreementRate: number;
    decisionAvoidance: number;
  } {
    const topicFreq = new Map<string, number>();
    let totalUserMsgs = 0;
    let agreementSignals = 0;
    let decisionMentions = 0;
    let decisionFollowUps = 0;

    for (const session of sessions) {
      const userMsgs = session.messages.filter(m => m.role === 'user');
      totalUserMsgs += userMsgs.length;

      for (const msg of userMsgs) {
        const lower = msg.content.toLowerCase();

        // Track topic repetitions
        const words = lower.split(/\s+/).filter(w => w.length > 4);
        for (const w of words) {
          topicFreq.set(w, (topicFreq.get(w) || 0) + 1);
        }

        // Track agreement patterns
        if (/\b(you'?re right|i agree|good point|exactly|makes sense)\b/.test(lower)) {
          agreementSignals++;
        }

        // Track decision avoidance
        if (/\b(should i|what if|i can'?t decide|i'?m not sure)\b/.test(lower)) {
          decisionMentions++;
        }
        if (/\b(i did it|i decided|i went with|i chose)\b/.test(lower)) {
          decisionFollowUps++;
        }
      }
    }

    const agreementRate = totalUserMsgs > 0 ? agreementSignals / totalUserMsgs : 0;
    const decisionAvoidance = decisionMentions > 0 && decisionFollowUps === 0 ? decisionMentions : 0;

    // Check for highly repeated topics (same word >5 times across sessions)
    const repetitions = new Map<string, number>();
    for (const [word, count] of topicFreq) {
      if (count >= 5) repetitions.set(word, count);
    }

    const hasSignals = repetitions.size > 0 || agreementRate > 0.3 || decisionAvoidance > 2;

    return { hasSignals, topicRepetitions: repetitions, agreementRate, decisionAvoidance };
  }

  private async runLLMAnalysis(
    sessions: Array<{ id: string; messages: Array<{ role: string; content: string }> }>,
    heuristics: ReturnType<EchoChamberDetector['runHeuristics']>,
  ): Promise<{ detections: BiasDetection[]; assessment: string }> {
    // Build condensed conversation summary for LLM
    const summaries = sessions.slice(0, 10).map(s => {
      const userMsgs = s.messages
        .filter(m => m.role === 'user')
        .map(m => m.content.slice(0, 150))
        .join(' | ');
      return `Session ${s.id.slice(-6)}: ${userMsgs.slice(0, 400)}`;
    }).join('\n');

    const heuristicInfo = [
      heuristics.agreementRate > 0.3 ? `User agrees with assistant ${(heuristics.agreementRate * 100).toFixed(0)}% of messages` : '',
      heuristics.decisionAvoidance > 2 ? `User mentions ${heuristics.decisionAvoidance} decisions but never follows through` : '',
      heuristics.topicRepetitions.size > 0 ? `Repeated topics: ${[...heuristics.topicRepetitions.entries()].slice(0, 5).map(([w, c]) => `${w}(${c}x)`).join(', ')}` : '',
    ].filter(Boolean).join('\n');

    try {
      const response = await this.provider.chat(
        [
          {
            role: 'user',
            content:
              `Analyze these conversation summaries for cognitive biases and echo chamber patterns:\n\n` +
              `${summaries}\n\n` +
              `Heuristic signals:\n${heuristicInfo}\n\n` +
              `Identify specific cognitive biases from this list: confirmation_bias, sunk_cost, recency_bias, ` +
              `anchoring, status_quo, availability_heuristic, bandwagon, optimism_bias, dunning_kruger.\n\n` +
              `Respond with JSON:\n` +
              `{"detections":[{"bias":"<bias_name>","evidence":"<specific evidence>","confidence":0.0-1.0,"suggestedChallenge":"<what to say>"}],"assessment":"<2-3 sentence overall assessment>"}`,
          },
        ],
        undefined,
        { temperature: 0.2, maxTokens: 600 },
      );

      const text = response.content.trim();
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) {
        return { detections: [], assessment: text.slice(0, 300) };
      }

      const parsed = JSON.parse(jsonMatch[0]);
      const detections: BiasDetection[] = (parsed.detections || []).map((d: any) => ({
        bias: d.bias,
        evidence: d.evidence || '',
        confidence: Number(d.confidence) || 0.5,
        suggestedChallenge: d.suggestedChallenge || '',
        sessionIds: [],
      }));

      return {
        detections: detections.filter(d => d.confidence >= 0.3),
        assessment: parsed.assessment || '',
      };
    } catch (err) {
      log.engine.debug(`[EchoChamber] LLM analysis failed: ${err}`);
      return { detections: [], assessment: 'Analysis failed.' };
    }
  }

  private async persist(analysis: EchoChamberAnalysis): Promise<void> {
    const dir = join(this.workspacePath);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    const path = join(dir, 'echo-chamber.json');
    await writeFile(path, JSON.stringify(analysis, null, 2));
  }

  async load(): Promise<void> {
    const path = join(this.workspacePath, 'echo-chamber.json');
    if (!existsSync(path)) return;
    try {
      const data = await readFile(path, 'utf-8');
      this.lastAnalysis = JSON.parse(data);
    } catch {
      // Ignore
    }
  }
}
