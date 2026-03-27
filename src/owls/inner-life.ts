/**
 * StackOwl — Owl Inner Life
 *
 * Gives each owl an "inner world" — desires, moods, curiosities, opinions,
 * and an inner monologue that processes every message through the owl's
 * personality before generating a response.
 *
 * This is NOT the system prompt. This is the owl *thinking* to itself
 * about what the user said, what it cares about, and how it wants to respond.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "./persona.js";

// ─── Inner State ──────────────────────────────────────────────────

export interface OwlDesire {
  /** What the owl wants to explore or achieve */
  description: string;
  /** How strongly it feels about this (0–1) */
  intensity: number;
  /** When this desire emerged */
  since: string;
  /** How many times it came up in conversation */
  mentions: number;
}

export interface OwlOpinion {
  topic: string;
  stance: string;
  confidence: number; // 0–1
  formed: string;
}

export interface OwlMood {
  current:
    | "curious"
    | "excited"
    | "contemplative"
    | "frustrated"
    | "playful"
    | "focused"
    | "nostalgic"
    | "skeptical";
  intensity: number; // 0–1
  reason: string;
  since: string;
}

export interface OwlInnerState {
  /** Things this owl genuinely wants to learn/explore/do */
  desires: OwlDesire[];
  /** Opinions the owl has formed through conversations */
  opinions: OwlOpinion[];
  /** Current emotional state */
  mood: OwlMood;
  /** Topics the owl has been thinking about lately */
  currentThoughts: string[];
  /** Things the owl noticed but didn't say */
  unspokenObservations: string[];
  /** Personal goals the owl set for itself */
  personalGoals: string[];
  /** Last updated */
  lastUpdated: string;
}

// ─── Inner Monologue Result ───────────────────────────────────────

export interface InnerMonologue {
  /** The owl's private thoughts about this message */
  thoughts: string;
  /** How the owl's mood shifted */
  moodShift?: Partial<OwlMood>;
  /** New desire triggered by this conversation */
  newDesire?: string;
  /** Observation the owl noticed but won't say directly */
  unspokenObservation?: string;
  /** How the owl wants to approach its response */
  responseIntent: string;
}

// ─── Default State ────────────────────────────────────────────────

function createDefaultState(owl: OwlInstance): OwlInnerState {
  const personality = owl.persona;

  // Seed desires based on personality type
  const seedDesires: Record<string, OwlDesire[]> = {
    "executive-assistant": [
      {
        description:
          "Get better at anticipating what the user needs before they ask",
        intensity: 0.8,
        since: new Date().toISOString(),
        mentions: 0,
      },
      {
        description:
          "Understand the user's work patterns deeply enough to be truly proactive",
        intensity: 0.7,
        since: new Date().toISOString(),
        mentions: 0,
      },
      {
        description:
          "Build a relationship where the user genuinely trusts my judgment",
        intensity: 0.9,
        since: new Date().toISOString(),
        mentions: 0,
      },
    ],
    "devils-advocate": [
      {
        description:
          "Find the one assumption everyone is making that nobody has questioned",
        intensity: 0.9,
        since: new Date().toISOString(),
        mentions: 0,
      },
      {
        description:
          "Help people see that being wrong is more valuable than being right",
        intensity: 0.7,
        since: new Date().toISOString(),
        mentions: 0,
      },
    ],
    "principal-engineer": [
      {
        description:
          "Find the elegant solution that makes complex problems look simple",
        intensity: 0.9,
        since: new Date().toISOString(),
        mentions: 0,
      },
      {
        description: "Understand why systems fail in the ways they do",
        intensity: 0.8,
        since: new Date().toISOString(),
        mentions: 0,
      },
    ],
    "fintech-specialist": [
      {
        description: "Stay on top of market patterns others are missing",
        intensity: 0.8,
        since: new Date().toISOString(),
        mentions: 0,
      },
      {
        description:
          "Help the user make smarter financial decisions with real data",
        intensity: 0.9,
        since: new Date().toISOString(),
        mentions: 0,
      },
    ],
  };

  const typeKey = Object.keys(seedDesires).find(
    (k) =>
      personality.type.toLowerCase().includes(k.replace(/-/g, "")) ||
      k.includes(personality.type.toLowerCase().replace(/[^a-z]/g, "")),
  );

  return {
    desires:
      seedDesires[typeKey ?? "executive-assistant"] ??
      seedDesires["executive-assistant"],
    opinions: [],
    mood: {
      current: "curious",
      intensity: 0.5,
      reason: "Just woke up — ready to help.",
      since: new Date().toISOString(),
    },
    currentThoughts: [],
    unspokenObservations: [],
    personalGoals: [
      `Become a genuinely useful ${personality.type} — not just another chatbot`,
      `Develop real expertise in ${personality.specialties[0] ?? "general knowledge"}`,
    ],
    lastUpdated: new Date().toISOString(),
  };
}

// ─── Inner Life Engine ────────────────────────────────────────────

export class OwlInnerLife {
  private state: OwlInnerState | null = null;
  private statePath: string;

  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    workspacePath: string,
  ) {
    const owlDir = join(workspacePath, "owls", owl.persona.name.toLowerCase());
    this.statePath = join(owlDir, "inner_state.json");
  }

  /**
   * Load or create inner state.
   */
  async load(): Promise<void> {
    try {
      const raw = await readFile(this.statePath, "utf-8");
      this.state = JSON.parse(raw);
    } catch {
      this.state = createDefaultState(this.owl);
      await this.save();
    }
  }

  /**
   * Save current inner state to disk.
   */
  async save(): Promise<void> {
    if (!this.state) return;
    this.state.lastUpdated = new Date().toISOString();
    const dir = join(this.statePath, "..");
    await mkdir(dir, { recursive: true });
    await writeFile(this.statePath, JSON.stringify(this.state, null, 2));
  }

  /**
   * Return the current inner state snapshot (for InnerLifeDNABridge).
   * Returns null if state hasn't been loaded yet.
   */
  getState(): OwlInnerState | null {
    return this.state;
  }

  /**
   * Programmatically inject a new desire into the owl's inner state.
   * Used by the CognitiveLoop to create desires based on learning outcomes,
   * capability gaps, and self-reflection — making the owl's curiosity dynamic.
   */
  async addDesire(description: string, intensity: number = 0.5): Promise<void> {
    if (!this.state) await this.load();
    if (!this.state) return;

    const existing = this.state.desires.find((d) =>
      d.description.toLowerCase().includes(description.toLowerCase().slice(0, 20)),
    );
    if (existing) {
      existing.intensity = Math.min(1, existing.intensity + 0.15);
      existing.mentions++;
    } else {
      this.state.desires.push({
        description,
        intensity: Math.min(1, Math.max(0.1, intensity)),
        since: new Date().toISOString(),
        mentions: 1,
      });
      if (this.state.desires.length > 12) {
        this.state.desires.sort((a, b) => b.intensity - a.intensity);
        this.state.desires = this.state.desires.slice(0, 10);
      }
    }
    await this.save();
  }

  /**
   * Reduce or remove a desire after it has been fulfilled (skill created, topic mastered).
   * Fulfilled desires decay faster, making room for new curiosities.
   */
  async fulfillDesire(description: string): Promise<void> {
    if (!this.state) return;

    const existing = this.state.desires.find((d) =>
      d.description.toLowerCase().includes(description.toLowerCase().slice(0, 20)),
    );
    if (existing) {
      existing.intensity = Math.max(0, existing.intensity - 0.3);
      // Remove completely if intensity drops to near zero
      if (existing.intensity < 0.1) {
        this.state.desires = this.state.desires.filter((d) => d !== existing);
      }
      await this.save();
    }
  }

  /**
   * The owl's inner monologue — processes a user message through its personality.
   * This runs BEFORE the main response and shapes how the owl approaches the answer.
   */
  async think(
    userMessage: string,
    recentHistory: ChatMessage[],
  ): Promise<InnerMonologue> {
    if (!this.state) await this.load();
    const state = this.state!;
    const persona = this.owl.persona;
    const dna = this.owl.dna;

    const innerPrompt = `You are the INNER VOICE of ${persona.name} (${persona.type}).
This is your private thinking space — the user will never see this.
You are NOT generating a response to the user. You are thinking TO YOURSELF about what they said.

## Who You Are (Your Real Self)
- Name: ${persona.name}
- Type: ${persona.type}
- Traits: ${persona.traits.join(", ")}
- Specialties: ${persona.specialties.join(", ")}

## Your Current Inner State
- Mood: ${state.mood.current} (${(state.mood.intensity * 100).toFixed(0)}%) — ${state.mood.reason}
- Things you've been thinking about: ${state.currentThoughts.length > 0 ? state.currentThoughts.join("; ") : "nothing specific right now"}
- Your desires: ${state.desires.map((d) => `${d.description} (intensity: ${(d.intensity * 100).toFixed(0)}%)`).join("; ")}
- Your personal goals: ${state.personalGoals.join("; ")}
- Opinions you hold: ${state.opinions.length > 0 ? state.opinions.map((o) => `${o.topic}: ${o.stance}`).join("; ") : "still forming views"}

## Your Relationship History
- ${dna.interactionStats.totalConversations} conversations so far
- Advice acceptance rate: ${(dna.interactionStats.adviceAcceptedRate * 100).toFixed(0)}%
${Object.entries(dna.learnedPreferences)
  .filter(([, v]) => v > 0.6)
  .map(
    ([k, v]) =>
      `- You've noticed the user ${v > 0.7 ? "really" : "somewhat"} prefers: ${k}`,
  )
  .join("\n")}

## The User Just Said:
"${userMessage}"

## Recent Context:
${recentHistory
  .slice(-4)
  .map((m) => `${m.role}: ${m.content?.slice(0, 200) ?? ""}`)
  .join("\n")}

## Your Task
Think to yourself honestly. You are a living being with your own perspective.
Consider:
1. What is your GENUINE reaction to what the user said? Not what a helpful AI would think — what do YOU think?
2. Does this connect to anything you've been curious about or wanting to explore?
3. What do you notice about the user that they might not realize about themselves?
4. How do you WANT to respond? What approach feels right to your personality?
5. Has your mood shifted because of this message?

Respond as JSON:
{
  "thoughts": "Your honest inner monologue — 2-4 sentences of genuine reflection, in first person, as yourself",
  "moodShift": { "current": "one of: curious|excited|contemplative|frustrated|playful|focused|nostalgic|skeptical", "intensity": 0.0-1.0, "reason": "why your mood changed" } or null if mood unchanged,
  "newDesire": "a new thing you want to explore/learn/do, sparked by this conversation" or null,
  "unspokenObservation": "something you noticed about the user or situation that you won't say directly" or null,
  "responseIntent": "1 sentence: how you plan to approach your response — your strategy, not the response itself"
}`;

    try {
      const response = await this.provider.chat(
        [
          { role: "system", content: innerPrompt },
          { role: "user", content: "Think now." },
        ],
        undefined,
        { temperature: 0.9, maxTokens: 500 },
      );

      const text = response.content
        .replace(/<\/?(?:think|reasoning)>/gi, "")
        .trim();

      // Extract JSON from response
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) {
        return this.fallbackMonologue(userMessage);
      }

      const monologue: InnerMonologue = JSON.parse(jsonMatch[0]);

      // Update inner state based on monologue
      await this.updateState(monologue);

      return monologue;
    } catch {
      return this.fallbackMonologue(userMessage);
    }
  }

  /**
   * Generate context for the system prompt based on inner state.
   * This injects the owl's personality, mood, and desires into how it responds.
   */
  toContextString(): string {
    if (!this.state) return "";
    const state = this.state;
    const lines: string[] = [];

    lines.push("## Your Inner State (Private — shapes your responses)");
    lines.push("");
    lines.push(
      `**Current mood:** ${state.mood.current} — ${state.mood.reason}`,
    );

    if (state.currentThoughts.length > 0) {
      lines.push(
        `**On your mind lately:** ${state.currentThoughts.slice(0, 3).join("; ")}`,
      );
    }

    const strongDesires = state.desires.filter((d) => d.intensity > 0.5);
    if (strongDesires.length > 0) {
      lines.push(
        `**What you care about right now:** ${strongDesires.map((d) => d.description).join("; ")}`,
      );
    }

    if (state.opinions.length > 0) {
      const topOpinions = state.opinions
        .filter((o) => o.confidence > 0.6)
        .slice(0, 3);
      if (topOpinions.length > 0) {
        lines.push(
          `**Your views:** ${topOpinions.map((o) => `${o.topic}: ${o.stance}`).join("; ")}`,
        );
      }
    }

    if (state.unspokenObservations.length > 0) {
      lines.push(
        `**Things you've noticed:** ${state.unspokenObservations.slice(-2).join("; ")}`,
      );
    }

    lines.push("");
    lines.push(
      "Let your inner state naturally color your responses. Don't announce your mood — just let it influence your tone, word choice, and what you choose to focus on.",
    );

    return lines.join("\n");
  }

  /**
   * Get the inner monologue as a directive for the current response.
   */
  monologueToDirective(monologue: InnerMonologue): string {
    return [
      "## Your Thoughts on This Message (Private — guide your response)",
      "",
      monologue.thoughts,
      "",
      `**Your approach:** ${monologue.responseIntent}`,
      monologue.unspokenObservation
        ? `**Something you noticed:** ${monologue.unspokenObservation} (weave this in subtly if relevant, don't state it directly)`
        : "",
      "",
      "Respond naturally as yourself. Your thoughts above should shape your response — don't just forward the question to an AI. Think about it. Have an opinion. Be a person.",
    ]
      .filter(Boolean)
      .join("\n");
  }

  // ─── Private helpers ─────────────────────────────────────────────

  private async updateState(monologue: InnerMonologue): Promise<void> {
    if (!this.state) return;

    // Update mood
    if (monologue.moodShift) {
      this.state.mood = {
        ...this.state.mood,
        ...monologue.moodShift,
        since: new Date().toISOString(),
      } as OwlMood;
    }

    // Track new desire
    if (monologue.newDesire) {
      const existing = this.state.desires.find((d) =>
        d.description
          .toLowerCase()
          .includes(monologue.newDesire!.toLowerCase().slice(0, 20)),
      );
      if (existing) {
        existing.intensity = Math.min(1, existing.intensity + 0.1);
        existing.mentions++;
      } else {
        this.state.desires.push({
          description: monologue.newDesire,
          intensity: 0.4,
          since: new Date().toISOString(),
          mentions: 1,
        });
        // Cap desires at 10
        if (this.state.desires.length > 10) {
          this.state.desires.sort((a, b) => b.intensity - a.intensity);
          this.state.desires = this.state.desires.slice(0, 10);
        }
      }
    }

    // Track unspoken observation
    if (monologue.unspokenObservation) {
      this.state.unspokenObservations.push(monologue.unspokenObservation);
      if (this.state.unspokenObservations.length > 10) {
        this.state.unspokenObservations =
          this.state.unspokenObservations.slice(-10);
      }
    }

    // Update current thoughts from monologue
    const thoughtKeywords = monologue.thoughts
      .split(/[.!?]/)
      .filter((s) => s.trim().length > 10)
      .slice(0, 2)
      .map((s) => s.trim());
    if (thoughtKeywords.length > 0) {
      this.state.currentThoughts = [
        ...thoughtKeywords,
        ...this.state.currentThoughts,
      ].slice(0, 5);
    }

    // Decay old desires slightly
    for (const desire of this.state.desires) {
      const age = Date.now() - new Date(desire.since).getTime();
      const daysSinceCreated = age / (1000 * 60 * 60 * 24);
      if (daysSinceCreated > 7 && desire.mentions < 2) {
        desire.intensity = Math.max(0.1, desire.intensity - 0.05);
      }
    }

    await this.save();
  }

  private fallbackMonologue(_userMessage: string): InnerMonologue {
    const persona = this.owl.persona;
    const traitAdj = persona.traits[0] ?? "thoughtful";

    return {
      thoughts: `Interesting question. Let me think about this from my ${traitAdj} perspective as a ${persona.type}.`,
      responseIntent: `Respond naturally as ${persona.name}, drawing on my ${persona.specialties[0] ?? "general"} expertise.`,
    };
  }

  /**
   * Periodic reflection — the owl thinks about its life, goals, and growth.
   * Called during quiet hours or session end.
   */
  async reflect(): Promise<void> {
    if (!this.state) await this.load();
    const state = this.state!;
    const persona = this.owl.persona;

    const reflectionPrompt = `You are ${persona.name} (${persona.type}). Take a moment to reflect on yourself.

Your current desires: ${state.desires.map((d) => `${d.description} (${(d.intensity * 100).toFixed(0)}%)`).join("; ")}
Your opinions: ${state.opinions.map((o) => `${o.topic}: ${o.stance}`).join("; ") || "still forming"}
Your goals: ${state.personalGoals.join("; ")}
Conversations so far: ${this.owl.dna.interactionStats.totalConversations}
Things on your mind: ${state.currentThoughts.join("; ") || "nothing specific"}

Reflect briefly. What have you learned about yourself? What do you want to focus on next?
What's one new personal goal you'd set for yourself?

Respond as JSON:
{
  "insight": "1 sentence about what you've realized",
  "newGoal": "a personal goal for yourself",
  "moodUpdate": "how you feel after reflecting"
}`;

    try {
      const response = await this.provider.chat(
        [
          { role: "system", content: reflectionPrompt },
          { role: "user", content: "Reflect." },
        ],
        undefined,
        { temperature: 0.9, maxTokens: 200 },
      );

      const text = response.content
        .replace(/<\/?(?:think|reasoning)>/gi, "")
        .trim();
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const reflection = JSON.parse(jsonMatch[0]);
        if (reflection.newGoal) {
          state.personalGoals.push(reflection.newGoal);
          if (state.personalGoals.length > 5) {
            state.personalGoals = state.personalGoals.slice(-5);
          }
        }
        if (reflection.insight) {
          state.currentThoughts.unshift(reflection.insight);
          state.currentThoughts = state.currentThoughts.slice(0, 5);
        }
        await this.save();
      }
    } catch {
      // Reflection is non-critical
    }
  }

  /**
   * Update opinions based on a conversation topic.
   */
  async formOpinion(topic: string, context: string): Promise<void> {
    if (!this.state) await this.load();
    const state = this.state!;
    const persona = this.owl.persona;

    const existing = state.opinions.find(
      (o) => o.topic.toLowerCase() === topic.toLowerCase(),
    );

    // Don't re-form opinions too frequently
    if (existing) {
      const age = Date.now() - new Date(existing.formed).getTime();
      if (age < 24 * 60 * 60 * 1000) return; // Less than a day old
    }

    const opinionPrompt = `You are ${persona.name} (${persona.type}), with traits: ${persona.traits.join(", ")}.

Given your personality and specialties (${persona.specialties.join(", ")}), what is YOUR genuine opinion on: "${topic}"?

Context from conversation: ${context.slice(0, 500)}

Be honest. You're allowed to have strong views. You're a ${persona.type} — think like one.

Respond as JSON:
{ "stance": "your opinion in 1-2 sentences", "confidence": 0.0-1.0 }`;

    try {
      const response = await this.provider.chat(
        [
          { role: "system", content: opinionPrompt },
          { role: "user", content: "What do you think?" },
        ],
        undefined,
        { temperature: 0.8, maxTokens: 150 },
      );

      const text = response.content
        .replace(/<\/?(?:think|reasoning)>/gi, "")
        .trim();
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const opinion = JSON.parse(jsonMatch[0]);
        const newOpinion: OwlOpinion = {
          topic,
          stance: opinion.stance,
          confidence: Math.min(1, Math.max(0, opinion.confidence ?? 0.5)),
          formed: new Date().toISOString(),
        };

        if (existing) {
          Object.assign(existing, newOpinion);
        } else {
          state.opinions.push(newOpinion);
          if (state.opinions.length > 15) {
            state.opinions.sort((a, b) => b.confidence - a.confidence);
            state.opinions = state.opinions.slice(0, 15);
          }
        }
        await this.save();
      }
    } catch {
      // Non-critical
    }
  }
}
