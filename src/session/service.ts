import type { MemoryDatabase } from "../memory/db.js";
import type { MessageCompressor } from "../memory/compressor.js";
import type { UserMemoryStore } from "./user-memory-store.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { ChatMessage } from "../providers/base.js";
import type { Session } from "../memory/store.js";
import { extractFactsFromConversation } from "./fact-extractor.js";

const MAX_SESSION_MESSAGES = 300;
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000;
const MIN_MESSAGES_FOR_EXTRACTION = 4;

// Regex for greeting patterns that trigger session reset
const GREETING_PATTERN = /^(hi|hello|hey|good morning|good afternoon|howdy|yo|sup)\b/i;

interface CacheEntry {
  session: Session;
  userId: string;
  owlName: string;
  lastActivity: number;
}

export interface SessionContext {
  summaryBlock: string;
  recentFacts: string;
  recentMessages: ChatMessage[];
}

export class SessionService {
  private cache = new Map<string, CacheEntry>();

  constructor(
    private db: MemoryDatabase,
    private compressor: MessageCompressor,
    private userMemoryStore: UserMemoryStore,
    private intelligence: IntelligenceRouter | undefined,
    private providerRegistry: ProviderRegistry,
    private fallbackProvider: string,
    private fallbackModel: string,
  ) {}

  async getOrCreate(sessionId: string, userId: string, owlName: string): Promise<Session> {
    const cached = this.cache.get(sessionId);
    if (cached) {
      cached.lastActivity = Date.now();
      return cached.session;
    }

    // Load from SQLite or create new
    const messages = this.db.messages.getSession(sessionId);
    const now = Date.now();
    const session: Session = {
      id: sessionId,
      messages,
      metadata: {
        owlName,
        startedAt: now,
        lastUpdatedAt: now,
      },
    };

    this.cache.set(sessionId, { session, userId, owlName, lastActivity: Date.now() });
    return session;
  }

  async addMessages(sessionId: string, messages: ChatMessage[]): Promise<void> {
    const entry = this.cache.get(sessionId);
    if (!entry) return;

    const { userId, owlName } = entry;
    entry.lastActivity = Date.now();

    // Append to SQLite
    this.db.messages.append(sessionId, userId, owlName, messages);

    // Enforce 300-message rolling window
    const count = this.db.messages.countSession(sessionId);
    if (count <= MAX_SESSION_MESSAGES) return;

    const overflow = count - MAX_SESSION_MESSAGES;
    const oldest = this.db.messages.getOldestN(sessionId, overflow);
    if (oldest.length === 0) return;

    const firstSeq = oldest[0].seq;
    const lastSeq = oldest[oldest.length - 1].seq;

    // Check if an existing summary already covers these messages
    const summary = this.db.summaries.getLatest(sessionId);
    const covered = summary !== null &&
      summary.fromSeq <= firstSeq &&
      summary.toSeq >= lastSeq;

    if (!covered) {
      // Summarize the full session messages before dropping
      const allMessages = this.db.messages.getSession(sessionId);
      await this.compressor.compress(sessionId, userId, owlName, allMessages);
    }

    this.db.messages.deleteByIds(oldest.map((r) => r.id));
  }

  async buildContext(sessionId: string, userId: string, lastUserText: string): Promise<SessionContext> {
    const recentMessages = this.db.messages.getRecent(sessionId, 50);

    // Build summary block from compressor
    const summaryBlock = this.compressor.buildContext(sessionId, recentMessages);

    // Get semantic facts for this user/query
    const factStrings = await this.userMemoryStore.retrieve(userId, lastUserText, 3);
    const recentFacts = factStrings.length > 0
      ? `<user_memory>\n${factStrings.map((f) => `- ${f}`).join("\n")}\n</user_memory>`
      : "";

    return { summaryBlock, recentFacts, recentMessages };
  }

  getUserId(sessionId: string): string | undefined {
    return this.cache.get(sessionId)?.userId;
  }

  evictFromCache(sessionId: string): void {
    this.cache.delete(sessionId);
  }

  evictStale(): string[] {
    const now = Date.now();
    const evicted: string[] = [];
    for (const [sessionId, entry] of this.cache.entries()) {
      if (now - entry.lastActivity >= SESSION_TIMEOUT_MS) {
        this.cache.delete(sessionId);
        evicted.push(sessionId);
      }
    }
    return evicted;
  }

  static isGreetingPattern(text: string): boolean {
    return GREETING_PATTERN.test(text.trim());
  }

  async extractAndStoreFacts(
    sessionId: string,
    userId: string,
    owlName: string,
    messages: ChatMessage[],
  ): Promise<void> {
    if (messages.length < MIN_MESSAGES_FOR_EXTRACTION) return;
    try {
      const facts = await extractFactsFromConversation(
        messages,
        this.intelligence,
        this.providerRegistry,
        this.fallbackProvider,
        this.fallbackModel,
      );
      for (const f of facts) {
        await this.userMemoryStore.add(userId, f.fact, f.category, owlName);
      }
    } catch {
      // Fire-and-forget — never throw
    }
  }

  destroy(): void {
    this.cache.clear();
  }
}
