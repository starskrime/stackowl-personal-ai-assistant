// src/routing/user-profile-service.ts
import type { MemoryDatabase } from "../memory/db.js";
import type { GoalGraph } from "../goals/graph.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { UserMemoryStore } from "../session/user-memory-store.js";
import { log } from "../logger.js";

export interface RoutingSignals {
  activePin: string | null;
  preferredStyle?: string;
  domainStack: string[];
  recentEpisodes: string[];
  relevantFacts: string[];
  trustLevel: "standard" | "elevated" | "restricted";
}

const SIGNAL_TIMEOUT_MS = 200;

function withTimeout<T>(p: Promise<T>, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<T>((res) => {
    timer = setTimeout(() => res(fallback), SIGNAL_TIMEOUT_MS);
  });
  return Promise.race([p.finally(() => clearTimeout(timer!)), timeout]);
}

export class UserProfileService {
  constructor(
    private db: Pick<MemoryDatabase, "userProfiles">,
    private goalGraph: GoalGraph | undefined,
    private episodicMemory: EpisodicMemory | undefined,
    private userMemoryStore: UserMemoryStore | undefined,
  ) {}

  async buildSignals(userId: string, userText: string): Promise<RoutingSignals> {
    const activePin = this.db.userProfiles.getPin(userId);
    const trustLevel = "standard" as const;
    // TODO(Phase 2): read trust_level from db.userProfiles.get(userId)

    const [domainStack, recentEpisodes, relevantFacts] = await Promise.all([
      withTimeout(this.getDomains(), []),
      withTimeout(this.getEpisodes(), []),
      withTimeout(this.getFacts(userId, userText), []),
    ]);

    return { activePin, domainStack, recentEpisodes, relevantFacts, trustLevel };
  }

  private async getDomains(): Promise<string[]> {
    if (!this.goalGraph) return [];
    try {
      return this.goalGraph.getActive().slice(0, 5).map((g) => g.title);
    } catch (err) {
      log.engine.debug(`[UserProfileService] domain fetch failed: ${err}`);
      return [];
    }
  }

  private async getEpisodes(): Promise<string[]> {
    if (!this.episodicMemory) return [];
    try {
      return this.episodicMemory.getRecent(3).map((e) => e.summary ?? "");
    } catch (err) {
      log.engine.debug(`[UserProfileService] episode fetch failed: ${err}`);
      return [];
    }
  }

  private async getFacts(userId: string, query: string): Promise<string[]> {
    if (!this.userMemoryStore) return [];
    try {
      return await this.userMemoryStore.retrieve(userId, query, 3);
    } catch (err) {
      log.engine.debug(`[UserProfileService] fact fetch failed: ${err}`);
      return [];
    }
  }
}
