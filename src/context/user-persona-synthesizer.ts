import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { StoredFact } from "../memory/fact-store.js";
import type { Episode } from "../memory/episodic.js";

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

export class UserPersonaSynthesizer {
  private pending = new Set<string>(); // userId → background synthesis in flight

  constructor(
    private provider: ModelProvider,
    private db: MemoryDatabase,
  ) {}

  async getPersona(
    userId: string,
    facts: StoredFact[],
    episodes: Episode[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    // Check cache first — return cached persona regardless of current fact count
    const cached = this.db.getUserPersona(userId);
    if (cached) {
      if (Date.now() < cached.expiresAt) {
        return JSON.parse(cached.personaJson) as UserPersona;
      }
      // Stale-while-revalidate: return stale, refresh in background
      if (!this.pending.has(userId)) {
        this.pending.add(userId);
        setImmediate(() => {
          this.synthesize(userId, facts, episodes, preferenceContext)
            .finally(() => this.pending.delete(userId));
        });
      }
      return JSON.parse(cached.personaJson) as UserPersona;
    }

    // No cache — need enough facts to synthesize
    if (facts.length < MIN_FACTS_FOR_PERSONA) return null;

    // No cache — synthesize synchronously (first-time user)
    return this.synthesize(userId, facts, episodes, preferenceContext);
  }

  async synthesize(
    userId: string,
    facts: StoredFact[],
    episodes: Episode[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    try {
      const topFacts = facts
        .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
        .map((f) => `- ${f.fact}`)
        .slice(0, 10)
        .join("\n");
      const topEpisodes = episodes
        .slice(0, 3)
        .map((e) => `- ${e.summary}`)
        .join("\n");

      const prompt = `You are analyzing a user to create a persona profile.

Facts about them:
${topFacts || "None yet"}

Recent episodes:
${topEpisodes || "None yet"}

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

      const persona: UserPersona = { ...JSON.parse(jsonMatch[0]), lastUpdated: new Date().toISOString() };
      this.db.setUserPersona(userId, JSON.stringify(persona), PERSONA_TTL_MS);
      return persona;
    } catch {
      return null;
    }
  }
}
