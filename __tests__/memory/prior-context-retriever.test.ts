import { describe, it, expect, vi, beforeEach } from "vitest";
import { PriorContextRetriever } from "../../src/memory/prior-context-retriever.js";
import type { EpisodicMemory } from "../../src/memory/episodic.js";
import type { Episode } from "../../src/memory/episodic.js";
import type { ModelProvider } from "../../src/providers/base.js";

const mockEpisode = (overrides: Partial<Episode> = {}): Episode => ({
  id: "ep1",
  sessionId: "session1",
  owlName: "Noctua",
  date: Date.now() - 86400000,
  summary: "User discussed testing",
  keyFacts: ["User prefers vitest", "Testing is important"],
  topics: ["testing"],
  userMessageCount: 3,
  ...overrides,
});

function makeMockEpisodicMemory(episodes: Episode[] = []): EpisodicMemory {
  return {
    search: vi.fn().mockResolvedValue(episodes),
    searchWithScoring: vi.fn().mockResolvedValue(
      episodes.map((ep) => ({ ...ep, relevanceScore: 0.5 })),
    ),
    getRecent: vi.fn().mockReturnValue(episodes),
  } as unknown as EpisodicMemory;
}

function makeMockProvider(): ModelProvider {
  return {
    embed: vi.fn().mockResolvedValue({ embedding: [0.1, 0.2, 0.3] }),
  } as unknown as ModelProvider;
}

describe("PriorContextRetriever", () => {
  let episodicMemory: EpisodicMemory;
  let provider: ModelProvider;
  let retriever: PriorContextRetriever;

  beforeEach(() => {
    episodicMemory = makeMockEpisodicMemory();
    provider = makeMockProvider();
    retriever = new PriorContextRetriever(episodicMemory, provider);
  });

  describe("hasTemporalReference()", () => {
    it("detects 'earlier' reference", () => {
      expect(retriever.hasTemporalReference("As I mentioned earlier...")).toBe(true);
    });

    it("detects 'before' reference", () => {
      expect(retriever.hasTemporalReference("Before we talked about...")).toBe(true);
    });

    it("detects 'last time' reference", () => {
      expect(retriever.hasTemporalReference("Last time we discussed...")).toBe(true);
    });

    it("detects multiple references", () => {
      expect(retriever.hasTemporalReference("As I mentioned earlier, before that...")).toBe(true);
    });

    it("returns false for regular message", () => {
      expect(retriever.hasTemporalReference("Hello, how are you?")).toBe(false);
    });

    it("is case insensitive", () => {
      expect(retriever.hasTemporalReference("EARLIER we talked about...")).toBe(true);
    });
  });

  describe("extractTemporalReferences()", () => {
    it("extracts matching temporal keywords", () => {
      const refs = retriever.extractTemporalReferences("As I mentioned earlier about this");
      expect(refs).toContain("earlier");
      expect(refs).toContain("mentioned");
    });

    it("returns empty array when no references", () => {
      const refs = retriever.extractTemporalReferences("Hello world");
      expect(refs).toHaveLength(0);
    });
  });

  describe("retrieve()", () => {
    it("returns empty result when no episodes match", async () => {
      const result = await retriever.retrieve({
        currentMessage: "What is the weather?",
      });

      expect(result.hasRelevantContext).toBe(false);
      expect(result.context).toBe("");
      expect(result.episodes).toHaveLength(0);
    });

    it("returns relevant context when episodes exist", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ summary: "User discussed testing" }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const result = await retriever.retrieve({
        currentMessage: "As I mentioned earlier, about testing...",
      });

      expect(result.hasRelevantContext).toBe(true);
      expect(result.episodes.length).toBeGreaterThan(0);
    });

    it("includes matched references in result", async () => {
      const result = await retriever.retrieve({
        currentMessage: "As I mentioned earlier, we talked about this",
      });

      expect(result.matchedReferences.length).toBeGreaterThan(0);
    });

    it("filters out current session when sessionId provided", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ sessionId: "current-session" }),
        mockEpisode({ sessionId: "old-session" }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const result = await retriever.retrieve({
        currentMessage: "Earlier we talked about...",
        sessionId: "current-session",
      });

      const currentSession = result.episodes.find(
        (ep) => ep.sessionId === "current-session",
      );
      expect(currentSession).toBeUndefined();
    });

    it("filters by owlName when provided", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ owlName: "Noctua" }),
        mockEpisode({ owlName: "OtherOwl" }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const result = await retriever.retrieve({
        currentMessage: "Earlier we talked about...",
        owlName: "Noctua",
      });

      expect(result.episodes.every((ep) => ep.owlName === "Noctua")).toBe(true);
    });
  });

  describe("buildContextPrompt()", () => {
    it("returns empty string when no relevant context", async () => {
      const prompt = await retriever.buildContextPrompt({
        currentMessage: "Hello",
      });

      expect(prompt).toBe("");
    });

    it("includes warning when temporal reference detected", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ summary: "User discussed testing" }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const prompt = await retriever.buildContextPrompt({
        currentMessage: "As I mentioned earlier about testing...",
      });

      expect(prompt).toContain("Previous Discussion");
      expect(prompt).toContain("User referenced prior conversation");
    });
  });

  describe("formatContext()", () => {
    it("formats episodes with date and summary", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ summary: "User discussed testing", date: new Date("2024-01-15").getTime() }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const result = await retriever.retrieve({
        currentMessage: "Earlier",
      });

      expect(result.context).toContain("2024");
      expect(result.context).toContain("testing");
    });

    it("includes key facts when available", async () => {
      episodicMemory = makeMockEpisodicMemory([
        mockEpisode({ keyFacts: ["User prefers vitest", "Testing is important"] }),
      ]);
      retriever = new PriorContextRetriever(episodicMemory, provider);

      const result = await retriever.retrieve({
        currentMessage: "Earlier",
      });

      expect(result.context).toContain("User prefers vitest");
    });
  });
});
