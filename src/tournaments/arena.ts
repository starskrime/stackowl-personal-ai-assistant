import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type { ModelProvider } from "../providers/base.js";
import type { Skill } from "../skills/types.js";
import type { SkillsRegistry } from "../skills/registry.js";

import type {
  Tournament,
  TournamentEntry,
  MatchResult,
  TournamentConfig,
} from "./types.js";

const DEFAULT_CONFIG: TournamentConfig = {
  minEntriesForTournament: 2,
  matchesPerRound: 3,
  promotionThreshold: 1400,
  retirementThreshold: 1000,
};

const K_FACTOR = 32;

const logger = new Logger("TOURNAMENT");

interface TournamentData {
  tournaments: Tournament[];
  entries: Record<string, TournamentEntry>;
}

export class SkillArena {
  private config: TournamentConfig;
  private data: TournamentData = { tournaments: [], entries: {} };
  private filePath: string;

  constructor(
    private provider: ModelProvider,
    private skillsRegistry: SkillsRegistry,
    private workspacePath: string,
    config?: Partial<TournamentConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.filePath = join(workspacePath, "tournaments.json");
  }

  async load(): Promise<void> {
    if (!existsSync(this.filePath)) {
      logger.info("No existing tournament data, starting fresh");
      return;
    }

    try {
      const raw = readFileSync(this.filePath, "utf-8");
      this.data = JSON.parse(raw) as TournamentData;
      logger.info(
        `Loaded ${this.data.tournaments.length} tournament(s) and ${Object.keys(this.data.entries).length} entrie(s)`,
      );
    } catch (err) {
      logger.warn(`Failed to load tournament data: ${err}`);
    }
  }

  findCompetitors(skill: Skill): Skill[] {
    const tags = (skill.metadata.openclaw as any)?.tags as string[] | undefined;
    if (!tags || tags.length === 0) return [];

    const tagSet = new Set(tags);
    return this.skillsRegistry.listEnabled().filter((other) => {
      if (other.name === skill.name) return false;
      const otherTags = (other.metadata.openclaw as any)?.tags as
        | string[]
        | undefined;
      if (!otherTags) return false;
      return otherTags.some((t) => tagSet.has(t));
    });
  }

  async runMatch(
    skillA: Skill,
    skillB: Skill,
    challenge: string,
  ): Promise<MatchResult> {
    const tournamentId = randomUUID();

    const [responseA, responseB] = await Promise.all([
      this.provider.chat(
        [
          { role: "system", content: skillA.instructions },
          { role: "user", content: challenge },
        ],
        undefined,
        { temperature: 0.7 },
      ),
      this.provider.chat(
        [
          { role: "system", content: skillB.instructions },
          { role: "user", content: challenge },
        ],
        undefined,
        { temperature: 0.7 },
      ),
    ]);

    const outputA = responseA.content;
    const outputB = responseB.content;

    const judgePrompt = `You are a skill quality judge. Compare two skill responses to the same challenge.

Challenge: ${challenge}

Response A (using skill "${skillA.name}"):
${outputA}

Response B (using skill "${skillB.name}"):
${outputB}

Score each response 0-10 on: accuracy, completeness, clarity, efficiency.
Then pick a winner.

Respond in JSON: {"scoreA": N, "scoreB": N, "winner": "A"|"B"|"draw", "reasoning": "..."}`;

    const judgeResponse = await this.provider.chat(
      [
        {
          role: "system",
          content: "You are an impartial judge. Respond only with valid JSON.",
        },
        { role: "user", content: judgePrompt },
      ],
      undefined,
      { temperature: 0.2 },
    );

    let verdict: {
      scoreA: number;
      scoreB: number;
      winner: "A" | "B" | "draw";
      reasoning: string;
    };
    try {
      const jsonMatch = judgeResponse.content.match(/\{[\s\S]*\}/);
      verdict = JSON.parse(jsonMatch ? jsonMatch[0] : judgeResponse.content);
    } catch {
      logger.warn("Failed to parse judge response, defaulting to draw");
      verdict = {
        scoreA: 5,
        scoreB: 5,
        winner: "draw",
        reasoning: "Failed to parse judge response",
      };
    }

    const entryA = this.ensureEntry(skillA);
    const entryB = this.ensureEntry(skillB);
    this.updateElo(entryA, entryB, verdict.winner);
    this.updateQualityScore(entryA, verdict.scoreA);
    this.updateQualityScore(entryB, verdict.scoreB);

    const result: MatchResult = {
      tournamentId,
      challenge,
      entryA: skillA.name,
      entryB: skillB.name,
      outputA,
      outputB,
      winner: verdict.winner,
      scoreA: verdict.scoreA,
      scoreB: verdict.scoreB,
      judgeReasoning: verdict.reasoning,
      timestamp: new Date().toISOString(),
    };

    logger.info(
      `Match: ${skillA.name} vs ${skillB.name} => winner: ${verdict.winner} (${verdict.scoreA}-${verdict.scoreB})`,
    );
    return result;
  }

  async runTournament(
    category: string,
    challenges: string[],
  ): Promise<Tournament> {
    const allSkills = this.skillsRegistry.listEnabled().filter((s) => {
      const tags = (s.metadata.openclaw as any)?.tags as string[] | undefined;
      return tags?.includes(category);
    });

    if (allSkills.length < this.config.minEntriesForTournament) {
      throw new Error(
        `Not enough skills in category "${category}" (found ${allSkills.length}, need ${this.config.minEntriesForTournament})`,
      );
    }

    const tournament: Tournament = {
      id: randomUUID(),
      category,
      entries: allSkills.map((s) => this.ensureEntry(s)),
      matches: [],
      status: "active",
      createdAt: new Date().toISOString(),
    };

    logger.info(
      `Starting tournament for category "${category}" with ${allSkills.length} skill(s) and ${challenges.length} challenge(s)`,
    );

    for (const challenge of challenges.slice(0, this.config.matchesPerRound)) {
      for (let i = 0; i < allSkills.length; i++) {
        for (let j = i + 1; j < allSkills.length; j++) {
          const result = await this.runMatch(
            allSkills[i],
            allSkills[j],
            challenge,
          );
          result.tournamentId = tournament.id;
          tournament.matches.push(result);
        }
      }
    }

    tournament.entries = allSkills.map((s) => this.ensureEntry(s));
    tournament.status = "completed";
    tournament.completedAt = new Date().toISOString();
    this.data.tournaments.push(tournament);

    logger.info(
      `Tournament "${tournament.id}" completed with ${tournament.matches.length} match(es)`,
    );
    return tournament;
  }

  getChampion(category: string): TournamentEntry | null {
    const candidates = Object.values(this.data.entries).filter((e) => {
      const skill = this.skillsRegistry.get(e.skillName);
      if (!skill) return false;
      const tags = (skill.metadata.openclaw as any)?.tags as
        | string[]
        | undefined;
      return tags?.includes(category);
    });

    if (candidates.length === 0) return null;

    candidates.sort((a, b) => b.elo - a.elo);
    const top = candidates[0];
    return top.elo >= this.config.promotionThreshold ? top : null;
  }

  getRetirementCandidates(): TournamentEntry[] {
    return Object.values(this.data.entries).filter(
      (e) =>
        e.elo < this.config.retirementThreshold &&
        e.wins + e.losses + e.draws > 0,
    );
  }

  getRankings(): TournamentEntry[] {
    return Object.values(this.data.entries).sort((a, b) => b.elo - a.elo);
  }

  async save(): Promise<void> {
    try {
      mkdirSync(this.workspacePath, { recursive: true });
      writeFileSync(this.filePath, JSON.stringify(this.data, null, 2), "utf-8");
      logger.info(`Persisted tournament data to ${this.filePath}`);
    } catch (err) {
      logger.error(`Failed to save tournament data: ${err}`);
    }
  }

  private ensureEntry(skill: Skill): TournamentEntry {
    const existing = this.data.entries[skill.name];
    if (existing) return existing;

    const entry: TournamentEntry = {
      skillName: skill.name,
      version: 1,
      instructions: skill.instructions,
      wins: 0,
      losses: 0,
      draws: 0,
      elo: 1200,
      avgQualityScore: 0,
      createdAt: new Date().toISOString(),
    };
    this.data.entries[skill.name] = entry;
    return entry;
  }

  private updateElo(
    entryA: TournamentEntry,
    entryB: TournamentEntry,
    winner: "A" | "B" | "draw",
  ): void {
    const expectedA = 1 / (1 + Math.pow(10, (entryB.elo - entryA.elo) / 400));
    const expectedB = 1 - expectedA;

    let scoreA: number;
    let scoreB: number;

    if (winner === "A") {
      scoreA = 1;
      scoreB = 0;
      entryA.wins++;
      entryB.losses++;
    } else if (winner === "B") {
      scoreA = 0;
      scoreB = 1;
      entryB.wins++;
      entryA.losses++;
    } else {
      scoreA = 0.5;
      scoreB = 0.5;
      entryA.draws++;
      entryB.draws++;
    }

    entryA.elo = Math.round(entryA.elo + K_FACTOR * (scoreA - expectedA));
    entryB.elo = Math.round(entryB.elo + K_FACTOR * (scoreB - expectedB));
  }

  private updateQualityScore(entry: TournamentEntry, newScore: number): void {
    const totalMatches = entry.wins + entry.losses + entry.draws;
    if (totalMatches <= 1) {
      entry.avgQualityScore = newScore;
    } else {
      entry.avgQualityScore =
        (entry.avgQualityScore * (totalMatches - 1) + newScore) / totalMatches;
    }
  }
}
