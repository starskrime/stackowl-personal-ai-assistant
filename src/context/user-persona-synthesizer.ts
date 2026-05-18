import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { Fact } from "../memory/fact-schema.js";
import { log } from "../logger.js";

export interface UserPersona {
  communicationStyle: "concise" | "verbose" | "technical" | "casual";
  expertiseLevel: "novice" | "intermediate" | "expert";
  currentProjects: string[];
  recurringPatterns: string[];
  emotionalTendencies: string;
  emotionalTrajectory: string[];
  preferredApproach: string;
  lastUpdated: string;
}

const PERSONA_TTL_MS = 30 * 60 * 1000; // 30 minutes
const MIN_FACTS_FOR_PERSONA = 3;

function parsePersona(json: string): UserPersona | null {
  try {
    const p = JSON.parse(json) as Record<string, unknown>;
    if (typeof p.communicationStyle !== "string" || typeof p.expertiseLevel !== "string") return null;
    return p as unknown as UserPersona;
  } catch (err) {
    log.memory.warn("user-persona-synthesizer: persona JSON parse failed", err);
    return null;
  }
}

export class UserPersonaSynthesizer {
  private pending = new Set<string>(); // userId → background synthesis in flight

  constructor(
    private provider: ModelProvider,
    private db: MemoryDatabase,
  ) {}

  async getPersona(
    userId: string,
    facts: Fact[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    // Check cache first — return cached persona regardless of current fact count
    const cached = this.db.getUserPersonaRaw(userId);
    if (cached) {
      if (Date.now() < cached.expiresAt) {
        const persona = parsePersona(cached.personaJson);
        if (persona) return persona;
        // Parse failed — fall through to synthesize
      } else {
        // Stale-while-revalidate: return stale, refresh in background
        const stale = parsePersona(cached.personaJson);
        if (stale) {
          if (!this.pending.has(userId)) {
            this.pending.add(userId);
            setImmediate(() => {
              this.synthesize(userId, facts, preferenceContext)
                .finally(() => this.pending.delete(userId));
            });
          }
          return stale;
        }
        // Stale parse failed — fall through to synthesize
      }
    }

    // No cache — need enough facts to synthesize
    if (facts.length < MIN_FACTS_FOR_PERSONA) return null;

    // No cache — synthesize synchronously (first-time user)
    return this.synthesize(userId, facts, preferenceContext);
  }

  async synthesize(
    userId: string,
    facts: Fact[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    try {
      const topFacts = facts
        .toSorted((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
        .slice(0, 10)
        .map((f) => `- ${f.content}`)
        .join("\n");

      const prompt = `You are analyzing a user to create a persona profile.

Facts about them:
${topFacts || "None yet"}

Preferences:
${preferenceContext || "None recorded"}

Respond with ONLY valid JSON matching this exact schema:
{
  "communicationStyle": "concise|verbose|technical|casual",
  "expertiseLevel": "novice|intermediate|expert",
  "currentProjects": ["project1"],
  "recurringPatterns": ["pattern1"],
  "emotionalTendencies": "one sentence",
  "emotionalTrajectory": ["mood (date)"],
  "preferredApproach": "one sentence",
  "lastUpdated": "ISO date"
}`;

      const response = await this.provider.chat(
        [{ role: "system", content: prompt }, { role: "user", content: "Generate persona." }],
        undefined,
        { temperature: 0.3, maxTokens: 400 },
      );

      const text = response.content.replace(/<\/?(?:think|reasoning)>/gi, "").trim();
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;

      const parsed = parsePersona(JSON.stringify({ ...JSON.parse(jsonMatch[0]), lastUpdated: new Date().toISOString() }));
      if (!parsed) return null;
      const persona = parsed;
      this.db.setUserPersona(userId, JSON.stringify(persona), PERSONA_TTL_MS);
      return persona;
    } catch (err) {
      log.engine.warn("UserPersonaSynthesizer: synthesis failed", { userId, error: err });
      return null;
    }
  }
}
