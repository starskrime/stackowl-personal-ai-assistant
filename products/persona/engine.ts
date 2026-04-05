/**
 * Persona Engine
 *
 * Multi-tenant DNA-based AI persona system.
 * Same brand voice, calibrated individually per user.
 *
 * Core model: userId × personaId matrix
 *   - Each persona has a base DNA definition (shared)
 *   - Each (userId, personaId) pair has its own evolved state
 *   - Trait bounds prevent drift outside acceptable range
 *
 * Usage:
 *   const engine = new PersonaEngine({ workspacePath: "./personas" });
 *   await engine.register(myPersonaDef);
 *
 *   // Get system prompt for a specific user
 *   const persona = await engine.render("user-123", "brand-voice-v1");
 *   // → persona.systemPrompt is injected into your LLM call
 *
 *   // After the conversation, evolve the persona based on what happened
 *   await engine.evolve("user-123", "brand-voice-v1", transcript, provider);
 */

import { join } from "node:path";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { v4 as uuidv4 } from "uuid";
import { applyBounds, applyDecay, computeDrift } from "./bounds.js";
import type {
  PersonaDefinition,
  PersonaState,
  PersonaTraits,
  PersonaSnapshot,
  RenderedPersona,
  PersonaAnalytics,
  TraitDelta,
  EvolutionEntry,
} from "./types.js";
import type { MemoryProvider } from "../memory-sdk/types.js";

export interface PersonaEngineConfig {
  workspacePath: string;
  defaultDecayRate?: number;
}

export class PersonaEngine {
  private config: PersonaEngineConfig;
  private definitions: Map<string, PersonaDefinition> = new Map();

  constructor(config: PersonaEngineConfig) {
    this.config = config;
  }

  // ─── Persona Definition Management ────────────────────────────────────────

  /**
   * Register a persona definition. Call this once on startup.
   */
  async register(definition: PersonaDefinition): Promise<void> {
    this.definitions.set(definition.id, definition);
    await this.saveDefinition(definition);
  }

  /**
   * Load all persona definitions from the workspace directory.
   */
  async loadDefinitions(): Promise<PersonaDefinition[]> {
    const dir = join(this.config.workspacePath, "definitions");
    if (!existsSync(dir)) return [];

    const { readdir } = await import("node:fs/promises");
    const files = await readdir(dir).catch(() => [] as string[]);
    const defs: PersonaDefinition[] = [];

    for (const file of files) {
      if (!file.endsWith(".json")) continue;
      try {
        const raw = await readFile(join(dir, file), "utf-8");
        const def = JSON.parse(raw) as PersonaDefinition;
        this.definitions.set(def.id, def);
        defs.push(def);
      } catch {
        // skip corrupt files
      }
    }

    return defs;
  }

  listDefinitions(): PersonaDefinition[] {
    return [...this.definitions.values()];
  }

  getDefinition(personaId: string): PersonaDefinition | undefined {
    return this.definitions.get(personaId);
  }

  // ─── Per-user State ────────────────────────────────────────────────────────

  /**
   * Get or create a persona state for a (userId, personaId) pair.
   */
  async getState(userId: string, personaId: string): Promise<PersonaState> {
    const definition = this.definitions.get(personaId);
    if (!definition) throw new Error(`Persona "${personaId}" not registered.`);

    const filePath = this.statePath(userId, personaId);

    if (existsSync(filePath)) {
      try {
        const raw = await readFile(filePath, "utf-8");
        return JSON.parse(raw) as PersonaState;
      } catch {
        // fall through to create fresh
      }
    }

    // Create fresh state from definition base traits
    const state: PersonaState = {
      personaId,
      userId,
      traits: { ...definition.baseTraits, domainExpertise: { ...definition.baseTraits.domainExpertise } },
      baseTraits: { ...definition.baseTraits, domainExpertise: { ...definition.baseTraits.domainExpertise } },
      generation: 0,
      lastEvolved: new Date().toISOString(),
      createdAt: new Date().toISOString(),
      evolutionLog: [],
      snapshots: [],
      interactionCount: 0,
    };

    await this.saveState(state);
    return state;
  }

  /**
   * Render a persona for a user — returns a system prompt with evolved traits.
   */
  async render(userId: string, personaId: string): Promise<RenderedPersona> {
    const definition = this.definitions.get(personaId);
    if (!definition) throw new Error(`Persona "${personaId}" not registered.`);

    const state = await this.getState(userId, personaId);

    // Apply decay if needed
    const decayedState = await this.applyDecayIfNeeded(state, definition);

    const systemPrompt = this.renderSystemPrompt(definition, decayedState.traits);

    return {
      personaId,
      userId,
      name: definition.name,
      systemPrompt,
      traits: decayedState.traits,
      generation: decayedState.generation,
      lastEvolved: decayedState.lastEvolved,
    };
  }

  /**
   * Evolve a persona based on a conversation transcript.
   * Uses LLM to analyze the interaction and suggest trait mutations.
   */
  async evolve(
    userId: string,
    personaId: string,
    transcript: Array<{ role: string; content: string }>,
    provider: MemoryProvider,
  ): Promise<{ mutated: boolean; mutations: string[] }> {
    const definition = this.definitions.get(personaId);
    if (!definition) throw new Error(`Persona "${personaId}" not registered.`);

    const state = await this.getState(userId, personaId);
    state.interactionCount++;

    // Only evolve every 3 interactions minimum
    if (state.interactionCount % 3 !== 0 && state.generation > 0) {
      await this.saveState(state);
      return { mutated: false, mutations: [] };
    }

    const prompt = this.buildEvolutionPrompt(definition, state, transcript);

    let mutations: string[] = [];
    let newTraits: Partial<PersonaTraits> = {};

    try {
      const response = await provider.chat(
        [
          {
            role: "system",
            content:
              "You are a persona evolution engine. Analyze a conversation transcript and suggest how a persona's traits should evolve based on what the user responded well to. " +
              "Return ONLY valid JSON. No explanation.",
          },
          { role: "user", content: prompt },
        ],
        { maxTokens: 512, temperature: 0.3 },
      );

      const parsed = this.parseEvolutionResponse(response.content);
      newTraits = parsed.traitUpdates ?? {};
      mutations = parsed.mutations ?? [];
    } catch {
      return { mutated: false, mutations: [] };
    }

    if (Object.keys(newTraits).length === 0) {
      await this.saveState(state);
      return { mutated: false, mutations: [] };
    }

    // Apply mutations with bounds
    const updatedTraits: PersonaTraits = { ...state.traits, domainExpertise: { ...state.traits.domainExpertise } };
    for (const [key, value] of Object.entries(newTraits)) {
      if (key in updatedTraits && typeof value === typeof (updatedTraits as Record<string, unknown>)[key]) {
        (updatedTraits as Record<string, unknown>)[key] = value;
      }
    }
    const bounded = applyBounds(updatedTraits, definition.bounds);

    state.traits = bounded;
    state.generation++;
    state.lastEvolved = new Date().toISOString();
    state.evolutionLog.push({
      generation: state.generation,
      timestamp: new Date().toISOString(),
      mutations,
      trigger: "conversation",
    });

    // Cap log to last 20 entries
    if (state.evolutionLog.length > 20) {
      state.evolutionLog = state.evolutionLog.slice(-20);
    }

    await this.saveState(state);
    return { mutated: true, mutations };
  }

  // ─── Snapshot / Rollback ──────────────────────────────────────────────────

  /**
   * Take a named snapshot of the current state.
   */
  async snapshot(userId: string, personaId: string, reason: string): Promise<PersonaSnapshot> {
    const state = await this.getState(userId, personaId);

    const snap: PersonaSnapshot = {
      snapshotId: uuidv4(),
      takenAt: new Date().toISOString(),
      reason,
      traits: { ...state.traits, domainExpertise: { ...state.traits.domainExpertise } },
      generation: state.generation,
    };

    state.snapshots.push(snap);
    // Keep last 10 snapshots
    if (state.snapshots.length > 10) {
      state.snapshots = state.snapshots.slice(-10);
    }

    await this.saveState(state);
    return snap;
  }

  /**
   * Roll back to a previous snapshot by ID.
   */
  async rollback(userId: string, personaId: string, snapshotId: string): Promise<boolean> {
    const definition = this.definitions.get(personaId);
    if (!definition) return false;

    const state = await this.getState(userId, personaId);
    const snap = state.snapshots.find((s) => s.snapshotId === snapshotId);
    if (!snap) return false;

    state.traits = applyBounds(snap.traits, definition.bounds);
    state.generation = snap.generation;
    state.lastEvolved = new Date().toISOString();
    state.evolutionLog.push({
      generation: state.generation,
      timestamp: new Date().toISOString(),
      mutations: [`Rolled back to snapshot "${snap.reason}" (gen ${snap.generation})`],
      trigger: "rollback",
    });

    await this.saveState(state);
    return true;
  }

  /**
   * Roll back to base traits (factory reset).
   */
  async reset(userId: string, personaId: string): Promise<void> {
    const definition = this.definitions.get(personaId);
    if (!definition) return;

    const state = await this.getState(userId, personaId);
    state.traits = { ...definition.baseTraits, domainExpertise: { ...definition.baseTraits.domainExpertise } };
    state.generation = 0;
    state.lastEvolved = new Date().toISOString();
    state.evolutionLog.push({
      generation: 0,
      timestamp: new Date().toISOString(),
      mutations: ["Reset to base traits"],
      trigger: "reset",
    });

    await this.saveState(state);
  }

  // ─── Analytics ────────────────────────────────────────────────────────────

  async analytics(userId: string, personaId: string): Promise<PersonaAnalytics> {
    const definition = this.definitions.get(personaId);
    if (!definition) throw new Error(`Persona "${personaId}" not registered.`);

    const state = await this.getState(userId, personaId);
    const drifts = computeDrift(state.baseTraits, state.traits);

    const traitDeltas: TraitDelta[] = drifts.map((d) => ({
      trait: d.trait,
      base: d.from,
      current: d.to,
      delta: typeof d.from === "number" && typeof d.to === "number"
        ? parseFloat((d.to - d.from).toFixed(3))
        : "changed",
    }));

    const topDomains = Object.entries(state.traits.domainExpertise)
      .map(([domain, expertise]) => ({ domain, expertise }))
      .sort((a, b) => b.expertise - a.expertise)
      .slice(0, 5);

    const recentMutations = state.evolutionLog
      .slice(-3)
      .flatMap((e) => e.mutations)
      .join("; ");

    const evolutionSummary =
      state.generation === 0
        ? "Persona has not evolved yet."
        : `Generation ${state.generation} — ${state.interactionCount} interactions. Recent changes: ${recentMutations || "none"}`;

    return {
      personaId,
      userId,
      generation: state.generation,
      interactionCount: state.interactionCount,
      traitDeltas,
      topDomains,
      evolutionSummary,
    };
  }

  /**
   * Export persona definition + user state as JSON.
   */
  async export(userId: string, personaId: string): Promise<{ definition: PersonaDefinition; state: PersonaState }> {
    const definition = this.definitions.get(personaId);
    if (!definition) throw new Error(`Persona "${personaId}" not registered.`);
    const state = await this.getState(userId, personaId);
    return { definition, state };
  }

  /**
   * Import a persona definition (and optionally a state).
   */
  async import(definition: PersonaDefinition, state?: PersonaState): Promise<void> {
    await this.register(definition);
    if (state) {
      await this.saveState(state);
    }
  }

  // ─── Private Helpers ──────────────────────────────────────────────────────

  private renderSystemPrompt(def: PersonaDefinition, traits: PersonaTraits): string {
    let prompt = def.systemPromptTemplate;

    // Replace trait placeholders
    const replacements: Record<string, string> = {
      "{{warmth}}": traits.warmth > 0.7 ? "very warm and caring" : traits.warmth > 0.4 ? "friendly" : "professional",
      "{{formality}}": traits.formality > 0.7 ? "formal" : traits.formality > 0.4 ? "semi-formal" : "casual",
      "{{humor}}": traits.humor > 0.6 ? "occasionally humorous" : "serious",
      "{{verbosity}}": traits.verbosity > 0.7 ? "detailed" : traits.verbosity > 0.4 ? "balanced" : "concise",
      "{{proactivity}}": traits.proactivity > 0.6 ? "proactively offers suggestions" : "responds to what is asked",
      "{{empathy}}": traits.empathy > 0.6 ? "highly empathetic" : "pragmatic",
      "{{directness}}": traits.directness > 0.6 ? "direct and assertive" : "diplomatic",
      "{{creativity}}": traits.creativity > 0.6 ? "creative and exploratory" : "methodical",
      "{{challenge_level}}": traits.challengeLevel,
      "{{teaching_style}}": traits.teachingStyle,
      "{{risk_tolerance}}": traits.riskTolerance,
    };

    for (const [placeholder, value] of Object.entries(replacements)) {
      prompt = prompt.replace(new RegExp(placeholder.replace(/[{}]/g, "\\$&"), "g"), value);
    }

    return prompt;
  }

  private buildEvolutionPrompt(
    definition: PersonaDefinition,
    state: PersonaState,
    transcript: Array<{ role: string; content: string }>,
  ): string {
    const transcriptText = transcript
      .slice(-12)
      .map((m) => `${m.role}: ${m.content.slice(0, 200)}`)
      .join("\n---\n");

    const currentTraits = JSON.stringify({
      warmth: state.traits.warmth,
      formality: state.traits.formality,
      humor: state.traits.humor,
      verbosity: state.traits.verbosity,
      proactivity: state.traits.proactivity,
      empathy: state.traits.empathy,
      directness: state.traits.directness,
      creativity: state.traits.creativity,
      challengeLevel: state.traits.challengeLevel,
      teachingStyle: state.traits.teachingStyle,
      riskTolerance: state.traits.riskTolerance,
    }, null, 2);

    const bounds = JSON.stringify(definition.bounds);

    return [
      `PERSONA: ${definition.name}`,
      `CURRENT TRAITS: ${currentTraits}`,
      `ALLOWED BOUNDS: ${bounds}`,
      ``,
      `CONVERSATION TRANSCRIPT:`,
      transcriptText,
      ``,
      `Analyze how the user responded to the persona's communication style.`,
      `Suggest small trait adjustments (max ±0.1 per numeric trait) that would improve future interactions.`,
      `Only suggest changes where there's a clear signal. Don't change everything.`,
      ``,
      `Return JSON:`,
      `{`,
      `  "traitUpdates": { "warmth": 0.75, ... },  // only traits to change`,
      `  "mutations": ["Increased warmth slightly — user responded well to personal tone", ...]`,
      `}`,
    ].join("\n");
  }

  private parseEvolutionResponse(raw: string): { traitUpdates?: Partial<PersonaTraits>; mutations?: string[] } {
    let text = raw.trim();
    if (text.startsWith("```")) {
      text = text.replace(/^```json?/, "").replace(/```$/, "").trim();
    }
    text = text.replace(/,\s*([}\]])/g, "$1");
    try {
      return JSON.parse(text) as { traitUpdates?: Partial<PersonaTraits>; mutations?: string[] };
    } catch {
      return {};
    }
  }

  private async applyDecayIfNeeded(state: PersonaState, definition: PersonaDefinition): Promise<PersonaState> {
    const decayRate = definition.decayRatePerWeek ?? this.config.defaultDecayRate ?? 0;
    if (decayRate <= 0) return state;

    const lastEvolved = new Date(state.lastEvolved).getTime();
    const weeksSince = (Date.now() - lastEvolved) / (7 * 24 * 60 * 60 * 1000);
    if (weeksSince < 1) return state;

    const decayed = applyDecay(state.traits, state.baseTraits, decayRate, weeksSince);
    const bounded = applyBounds(decayed, definition.bounds);
    state.traits = bounded;
    state.lastEvolved = new Date().toISOString();
    await this.saveState(state);
    return state;
  }

  private statePath(userId: string, personaId: string): string {
    return join(this.config.workspacePath, "states", `${userId}__${personaId}.json`);
  }

  private async saveState(state: PersonaState): Promise<void> {
    const dir = join(this.config.workspacePath, "states");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(this.statePath(state.userId, state.personaId), JSON.stringify(state, null, 2));
  }

  private async saveDefinition(def: PersonaDefinition): Promise<void> {
    const dir = join(this.config.workspacePath, "definitions");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(join(dir, `${def.id}.json`), JSON.stringify(def, null, 2));
  }
}
