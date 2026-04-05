/**
 * Deliberation Engine
 *
 * Standalone adversarial AI debate engine for high-stakes decisions.
 * No OwlEngine / OwlInstance dependency — works with any ModelProvider.
 *
 * Flow:
 *   Input decision topic
 *   → Round 1: Each voice states position (FOR/AGAINST/CONDITIONAL/ANALYSIS)
 *   → Round 2: Cross-examination — voices challenge weakest arguments
 *   → Round 3: Synthesis — final verdict with dissenting views documented
 *
 * Usage:
 *   const engine = new DeliberationEngine(provider);
 *   const result = await engine.debate("Should we rewrite the backend in Rust?");
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../../src/providers/base.js";
import {
  type VoicePreset,
  getDefaultVoiceSet,
  getAllVoices,
  getVoice,
} from "./voices.js";

export type DebatePosition =
  | "FOR"
  | "AGAINST"
  | "CONDITIONAL"
  | "NEUTRAL"
  | "ANALYSIS";
export type DebateVerdict =
  | "PROCEED"
  | "HOLD"
  | "ABORT"
  | "REVISE"
  | "SPLIT";

export interface VoicePosition {
  voiceId: string;
  voiceName: string;
  voiceEmoji: string;
  position: DebatePosition;
  argument: string;
}

export interface VoiceChallenge {
  voiceId: string;
  voiceName: string;
  voiceEmoji: string;
  targetVoiceId: string;
  targetVoiceName: string;
  challenge: string;
}

export interface DebateRound {
  round: 1 | 2 | 3;
  label: string;
  completedAt?: number;
}

export interface DissentingView {
  voiceId: string;
  voiceName: string;
  concern: string;
}

export interface DebateResult {
  id: string;
  topic: string;
  verdict: DebateVerdict;
  synthesis: string;
  dissentingViews: DissentingView[];
  positions: VoicePosition[];
  challenges: VoiceChallenge[];
  voices: VoicePreset[];
  durationMs: number;
  startedAt: number;
  completedAt: number;
}

export interface DebateCallbacks {
  onRoundStart?: (round: DebateRound) => void;
  onPositionReady?: (position: VoicePosition) => void;
  onChallengeReady?: (challenge: VoiceChallenge) => void;
  onSynthesisReady?: (verdict: DebateVerdict, synthesis: string) => void;
  onError?: (error: Error) => void;
}

export interface DebateOptions {
  voiceIds?: string[];
  customVoices?: VoicePreset[];
  callbacks?: DebateCallbacks;
  maxTokensPerTurn?: number;
  model?: string;
}

export class DeliberationEngine {
  private provider: ModelProvider;

  constructor(provider: ModelProvider) {
    this.provider = provider;
  }

  /**
   * Run a full 3-round debate on the given topic.
   */
  async debate(
    topic: string,
    options: DebateOptions = {},
  ): Promise<DebateResult> {
    const startedAt = Date.now();
    const id = uuidv4();
    const cb = options.callbacks;

    // Resolve voices
    const voices = this.resolveVoices(options);

    if (voices.length < 2) {
      throw new Error("A debate requires at least 2 voices.");
    }

    const result: Omit<DebateResult, "durationMs" | "completedAt"> = {
      id,
      topic,
      verdict: "HOLD",
      synthesis: "",
      dissentingViews: [],
      positions: [],
      challenges: [],
      voices,
      startedAt,
    };

    // Round 1: Initial Positions
    cb?.onRoundStart?.({ round: 1, label: "Initial Positions" });
    await this.runRound1(topic, voices, result, options, cb);

    // Round 2: Cross-Examination
    cb?.onRoundStart?.({ round: 2, label: "Cross-Examination" });
    await this.runRound2(topic, voices, result, options, cb);

    // Round 3: Synthesis
    cb?.onRoundStart?.({ round: 3, label: "Synthesis & Verdict" });
    await this.runRound3(topic, result, options, cb);

    const completedAt = Date.now();
    return {
      ...result,
      completedAt,
      durationMs: completedAt - startedAt,
    };
  }

  private async runRound1(
    topic: string,
    voices: VoicePreset[],
    result: Omit<DebateResult, "durationMs" | "completedAt">,
    options: DebateOptions,
    cb?: DebateCallbacks,
  ): Promise<void> {
    for (const voice of voices) {
      const prompt =
        `DECISION TOPIC: "${topic}"\n\n` +
        `Your role: ${voice.name} (${voice.role})\n\n` +
        `Task: State your position on this decision.\n` +
        `First word must be one of: FOR, AGAINST, CONDITIONAL, NEUTRAL, or ANALYSIS\n` +
        `Then give your argument in 2-3 sentences. Be specific and opinionated — no hedging.`;

      const response = await this.provider.chat(
        [
          { role: "system", content: voice.systemPrompt },
          { role: "user", content: prompt },
        ],
        options.model,
        { maxTokens: options.maxTokensPerTurn ?? 256 },
      );

      const position = this.extractPosition(response.content);
      const argument = this.stripPositionTag(response.content, position);

      const vp: VoicePosition = {
        voiceId: voice.id,
        voiceName: voice.name,
        voiceEmoji: voice.emoji,
        position,
        argument,
      };

      result.positions.push(vp);
      cb?.onPositionReady?.(vp);
    }
  }

  private async runRound2(
    topic: string,
    voices: VoicePreset[],
    result: Omit<DebateResult, "durationMs" | "completedAt">,
    options: DebateOptions,
    cb?: DebateCallbacks,
  ): Promise<void> {
    // Summarize positions for challengers
    const positionSummary = result.positions
      .map((p) => `- ${p.voiceEmoji} ${p.voiceName} [${p.position}]: ${p.argument}`)
      .join("\n\n");

    // Select challenger voices: AGAINST > CONDITIONAL > others
    // Each voice challenges the weakest opposing argument
    const challengers = this.selectChallengers(voices, result.positions);

    for (const challenger of challengers) {
      const voice = voices.find((v) => v.id === challenger.voiceId)!;

      const prompt =
        `DECISION TOPIC: "${topic}"\n\n` +
        `Current positions in the debate:\n${positionSummary}\n\n` +
        `Your role: ${voice.name} (${voice.role})\n\n` +
        `Task: Challenge the weakest or most naive argument above. ` +
        `Start by naming the voice you're challenging (e.g., "Challenging [Optimist]..."). ` +
        `Point out the specific flaw in 2-3 sentences. Be sharp and precise.`;

      const response = await this.provider.chat(
        [
          { role: "system", content: voice.systemPrompt },
          { role: "user", content: prompt },
        ],
        options.model,
        { maxTokens: options.maxTokensPerTurn ?? 256 },
      );

      // Try to detect which voice was challenged
      const targetVoice = this.detectChallengeTarget(
        response.content,
        voices,
        voice.id,
      );

      const vc: VoiceChallenge = {
        voiceId: voice.id,
        voiceName: voice.name,
        voiceEmoji: voice.emoji,
        targetVoiceId: targetVoice?.id ?? "",
        targetVoiceName: targetVoice?.name ?? "the group",
        challenge: response.content,
      };

      result.challenges.push(vc);
      cb?.onChallengeReady?.(vc);
    }
  }

  private async runRound3(
    topic: string,
    result: Omit<DebateResult, "durationMs" | "completedAt">,
    options: DebateOptions,
    cb?: DebateCallbacks,
  ): Promise<void> {
    const positionSummary = result.positions
      .map((p) => `- ${p.voiceEmoji} ${p.voiceName} [${p.position}]: ${p.argument}`)
      .join("\n\n");

    const challengeSummary =
      result.challenges.length > 0
        ? result.challenges
            .map((c) => `- ${c.voiceEmoji} ${c.voiceName} challenged ${c.targetVoiceName}: ${c.challenge}`)
            .join("\n\n")
        : "No formal challenges raised.";

    const synthesisPrompt =
      `DECISION TOPIC: "${topic}"\n\n` +
      `ROUND 1 — Positions:\n${positionSummary}\n\n` +
      `ROUND 2 — Cross-Examination:\n${challengeSummary}\n\n` +
      `Task: Synthesize this debate into a final verdict. Your response MUST:\n` +
      `1. Start with one of: PROCEED, HOLD, ABORT, REVISE, or SPLIT (split = do part of it)\n` +
      `2. Give the core reasoning in 2-3 sentences\n` +
      `3. List 2-3 key conditions or caveats if any\n` +
      `4. Note which voices had the strongest dissenting views (if any)\n\n` +
      `Be decisive. Do NOT give a non-answer. The decision-maker needs a clear recommendation.`;

    const response = await this.provider.chat(
      [
        {
          role: "system",
          content:
            "You are a neutral synthesis judge in a structured debate. Your job is to weigh all arguments and deliver a clear, actionable verdict. " +
            "You must make a call even when evidence is mixed. Document dissent clearly.",
        },
        { role: "user", content: synthesisPrompt },
      ],
      options.model,
      { maxTokens: options.maxTokensPerTurn ?? 512 },
    );

    result.synthesis = response.content;

    // Extract verdict
    const verdictMatch = response.content.match(
      /\b(PROCEED|HOLD|ABORT|REVISE|SPLIT)\b/i,
    );
    result.verdict = verdictMatch
      ? (verdictMatch[1].toUpperCase() as DebateVerdict)
      : "HOLD";

    // Extract dissenting views
    result.dissentingViews = this.extractDissentingViews(
      response.content,
      result.voices,
    );

    cb?.onSynthesisReady?.(result.verdict, result.synthesis);
  }

  /**
   * Format a completed debate result as markdown.
   */
  formatMarkdown(result: DebateResult): string {
    const lines: string[] = [];

    lines.push(`# 🏛️ Deliberation: ${result.topic}`);
    lines.push(`**Verdict:** ${result.verdict} | **Duration:** ${(result.durationMs / 1000).toFixed(1)}s`);
    lines.push("");

    lines.push("## Round 1 — Initial Positions");
    for (const p of result.positions) {
      lines.push(`**${p.voiceEmoji} ${p.voiceName}** [${p.position}]`);
      lines.push(`> ${p.argument}`);
      lines.push("");
    }

    if (result.challenges.length > 0) {
      lines.push("## Round 2 — Cross-Examination");
      for (const c of result.challenges) {
        lines.push(`**${c.voiceEmoji} ${c.voiceName}** → *challenging ${c.targetVoiceName}*`);
        lines.push(`> ${c.challenge}`);
        lines.push("");
      }
    }

    lines.push("## Round 3 — Synthesis & Verdict");
    lines.push(result.synthesis);
    lines.push("");

    if (result.dissentingViews.length > 0) {
      lines.push("## Dissenting Views");
      for (const d of result.dissentingViews) {
        lines.push(`- **${d.voiceName}**: ${d.concern}`);
      }
    }

    return lines.join("\n");
  }

  private resolveVoices(options: DebateOptions): VoicePreset[] {
    if (options.customVoices && options.customVoices.length > 0) {
      return options.customVoices;
    }
    if (options.voiceIds && options.voiceIds.length > 0) {
      return options.voiceIds
        .map((id) => getVoice(id))
        .filter((v): v is VoicePreset => v !== undefined);
    }
    return getDefaultVoiceSet();
  }

  private selectChallengers(
    voices: VoicePreset[],
    positions: VoicePosition[],
  ): Array<{ voiceId: string }> {
    // Pick voices with AGAINST/CONDITIONAL positions first, then fill to 2 challengers max
    const against = positions.filter((p) =>
      ["AGAINST", "CONDITIONAL"].includes(p.position),
    );
    const challengers: Array<{ voiceId: string }> = [];
    const seen = new Set<string>();

    for (const p of against) {
      if (challengers.length >= 2) break;
      if (!seen.has(p.voiceId)) {
        challengers.push({ voiceId: p.voiceId });
        seen.add(p.voiceId);
      }
    }

    // Fill with remaining voices if we don't have 2
    if (challengers.length < 2) {
      for (const v of voices) {
        if (challengers.length >= 2) break;
        if (!seen.has(v.id)) {
          challengers.push({ voiceId: v.id });
          seen.add(v.id);
        }
      }
    }

    return challengers;
  }

  private detectChallengeTarget(
    content: string,
    voices: VoicePreset[],
    excludeId: string,
  ): VoicePreset | undefined {
    const lower = content.toLowerCase();
    for (const v of voices) {
      if (v.id === excludeId) continue;
      if (lower.includes(v.name.toLowerCase()) || lower.includes(v.id.replace(/_/g, " "))) {
        return v;
      }
    }
    return undefined;
  }

  private extractPosition(content: string): DebatePosition {
    const upper = content.toUpperCase();
    const tags: DebatePosition[] = ["FOR", "AGAINST", "CONDITIONAL", "NEUTRAL", "ANALYSIS"];
    for (const tag of tags) {
      if (upper.startsWith(tag) || upper.includes(`[${tag}]`)) {
        return tag;
      }
    }
    return "ANALYSIS";
  }

  private stripPositionTag(content: string, position: DebatePosition): string {
    return content
      .replace(new RegExp(`^${position}[:\\s]*`, "i"), "")
      .replace(new RegExp(`\\[${position}\\]`, "gi"), "")
      .trim();
  }

  private extractDissentingViews(
    synthesis: string,
    voices: VoicePreset[],
  ): DissentingView[] {
    const views: DissentingView[] = [];
    const lower = synthesis.toLowerCase();

    for (const voice of voices) {
      const nameIdx = lower.indexOf(voice.name.toLowerCase());
      if (nameIdx === -1) continue;

      // Extract a short snippet around the mention
      const start = Math.max(0, nameIdx - 10);
      const end = Math.min(synthesis.length, nameIdx + voice.name.length + 150);
      const snippet = synthesis.substring(start, end).trim();

      if (snippet.length > 0) {
        views.push({
          voiceId: voice.id,
          voiceName: voice.name,
          concern: snippet,
        });
      }
    }

    return views;
  }

  /**
   * List all available built-in voices.
   */
  static listVoices(): VoicePreset[] {
    return getAllVoices();
  }
}
