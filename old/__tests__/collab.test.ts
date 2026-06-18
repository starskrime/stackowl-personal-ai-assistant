import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  CollabSessionManager,
  CollabFacilitator,
  type SharedSession,
  type CollabMessage,
  type Participant,
  type SessionSettings,
  type CollabConfig,
} from "../src/collab/index.js";
import type { ModelProvider, ChatResponse } from "../src/providers/base.js";
import {
  existsSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";

vi.mock("node:fs", () => ({
  existsSync: vi.fn(),
  mkdirSync: vi.fn(),
  writeFileSync: vi.fn(),
  readFileSync: vi.fn(),
  readdirSync: vi.fn(),
}));

vi.mock("../src/logger.js", () => ({
  Logger: vi.fn().mockImplementation(() => ({
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  })),
}));

describe("CollabSessionManager", () => {
  const testWorkspace = "/test/workspace";

  const mockOwner = {
    userId: "user-1",
    displayName: "Alice",
    channelId: "channel-1",
  };

  const createMockProvider = (): ModelProvider => ({
    chat: vi.fn(),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
    name: "mock",
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(existsSync).mockReturnValue(false);
    vi.mocked(mkdirSync).mockReturnValue(undefined);
    vi.mocked(writeFileSync).mockReturnValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("createSession", () => {
    it("should create a new session with owner as participant", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test Session", "Hooty", mockOwner);

      expect(session.name).toBe("Test Session");
      expect(session.owlName).toBe("Hooty");
      expect(session.participants).toHaveLength(1);
      expect(session.participants[0].role).toBe("owner");
      expect(session.participants[0].displayName).toBe("Alice");
      expect(session.messages).toHaveLength(0);
      expect(session.settings.decisionMode).toBe("owner_decides");
    });

    it("should apply custom settings", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner, {
        decisionMode: "majority",
        maxParticipants: 5,
      });

      expect(session.settings.decisionMode).toBe("majority");
      expect(session.settings.maxParticipants).toBe(5);
    });

    it("should throw when max active sessions reached", () => {
      const manager = new CollabSessionManager(testWorkspace, {
        maxActiveSessions: 1,
      });

      manager.createSession("Session 1", "Hooty", mockOwner);

      expect(() =>
        manager.createSession("Session 2", "Hooty", mockOwner),
      ).toThrow("Maximum active sessions");
    });

    it("should save session to disk", () => {
      const manager = new CollabSessionManager(testWorkspace);
      manager.createSession("Test", "Hooty", mockOwner);

      expect(mkdirSync).toHaveBeenCalledWith(
        join(testWorkspace, "collab-sessions"),
        { recursive: true },
      );
      expect(writeFileSync).toHaveBeenCalled();
    });

    it("should generate unique session IDs", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session1 = manager.createSession("Test1", "Hooty", mockOwner);
      const session2 = manager.createSession("Test2", "Hooty", mockOwner);

      expect(session1.id).not.toBe(session2.id);
    });
  });

  describe("joinSession", () => {
    it("should add participant to existing session", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      const updated = manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      expect(updated.participants).toHaveLength(2);
      expect(updated.participants[1].displayName).toBe("Bob");
      expect(updated.participants[1].role).toBe("member");
    });

    it("should update lastActiveAt if user re-joins", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);
      const originalJoinedAt = session.participants[0].joinedAt;

      const updated = manager.joinSession(session.id, mockOwner);

      expect(updated.participants).toHaveLength(1);
      expect(updated.participants[0].joinedAt).toBe(originalJoinedAt);
    });

    it("should throw when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(() => manager.joinSession("nonexistent", mockOwner)).toThrow(
        "Session nonexistent not found",
      );
    });

    it("should throw when observers not allowed", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner, {
        allowObservers: false,
      });

      expect(() =>
        manager.joinSession(
          session.id,
          {
            userId: "user-2",
            displayName: "Bob",
            channelId: "channel-2",
          },
          "observer",
        ),
      ).toThrow("does not allow observers");
    });

    it("should throw when session is full", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner, {
        maxParticipants: 2,
      });

      manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      expect(() =>
        manager.joinSession(session.id, {
          userId: "user-3",
          displayName: "Carol",
          channelId: "channel-3",
        }),
      ).toThrow("Session is full");
    });
  });

  describe("leaveSession", () => {
    it("should remove participant from session", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      manager.leaveSession(session.id, "user-2");

      const updated = manager.getSession(session.id)!;
      expect(updated.participants).toHaveLength(1);
      expect(updated.participants[0].userId).toBe("user-1");
    });

    it("should transfer ownership when owner leaves", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      manager.leaveSession(session.id, "user-1");

      const updated = manager.getSession(session.id)!;
      expect(
        updated.participants.find((p) => p.userId === "user-2")?.role,
      ).toBe("owner");
    });

    it("should delete session when last participant leaves", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.leaveSession(session.id, "user-1");

      expect(manager.getSession(session.id)).toBeNull();
    });

    it("should do nothing when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(() => manager.leaveSession("nonexistent", "user-1")).not.toThrow();
    });
  });

  describe("addMessage", () => {
    it("should add message to session", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      const message = manager.addMessage(session.id, {
        userId: "user-1",
        displayName: "Alice",
        role: "user",
        content: "Hello everyone!",
      });

      expect(message.content).toBe("Hello everyone!");
      expect(message.id).toBeDefined();
      expect(message.timestamp).toBeDefined();
    });

    it("should update participant lastActiveAt", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.addMessage(session.id, {
        userId: "user-1",
        displayName: "Alice",
        role: "user",
        content: "Hello!",
      });

      const updated = manager.getSession(session.id)!;
      expect(updated.participants[0].lastActiveAt).toBeDefined();
    });

    it("should throw when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(() =>
        manager.addMessage("nonexistent", {
          userId: "user-1",
          displayName: "Alice",
          role: "user",
          content: "Hello",
        }),
      ).toThrow("Session nonexistent not found");
    });

    it("should throw when message limit reached", () => {
      const manager = new CollabSessionManager(testWorkspace, {
        maxMessagesPerSession: 2,
      });
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.addMessage(session.id, {
        userId: "user-1",
        displayName: "Alice",
        role: "user",
        content: "Message 1",
      });
      manager.addMessage(session.id, {
        userId: "user-1",
        displayName: "Alice",
        role: "user",
        content: "Message 2",
      });

      expect(() =>
        manager.addMessage(session.id, {
          userId: "user-1",
          displayName: "Alice",
          role: "user",
          content: "Message 3",
        }),
      ).toThrow("Session message limit");
    });
  });

  describe("getSession", () => {
    it("should return session by ID", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      const found = manager.getSession(session.id);

      expect(found?.name).toBe("Test");
    });

    it("should return null when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(manager.getSession("nonexistent")).toBeNull();
    });
  });

  describe("listSessions", () => {
    it("should return all active sessions", () => {
      const manager = new CollabSessionManager(testWorkspace);
      manager.createSession("Session 1", "Hooty", mockOwner);
      manager.createSession("Session 2", "Hooty", mockOwner);

      const sessions = manager.listSessions();

      expect(sessions).toHaveLength(2);
    });
  });

  describe("getUserSessions", () => {
    it("should return sessions for a specific user", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      const bobSessions = manager.getUserSessions("user-2");

      expect(bobSessions).toHaveLength(1);
      expect(
        bobSessions[0].participants.some((p) => p.userId === "user-2"),
      ).toBe(true);
    });

    it("should return empty array when user has no sessions", () => {
      const manager = new CollabSessionManager(testWorkspace);
      manager.createSession("Test", "Hooty", mockOwner);

      expect(manager.getUserSessions("nonexistent")).toHaveLength(0);
    });
  });

  describe("endSession", () => {
    it("should delete session and return it", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);

      const ended = manager.endSession(session.id);

      expect(ended.name).toBe("Test");
      expect(manager.getSession(session.id)).toBeNull();
    });

    it("should throw when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(() => manager.endSession("nonexistent")).toThrow(
        "Session nonexistent not found",
      );
    });
  });

  describe("buildCollabContext", () => {
    it("should generate XML context for session", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);
      manager.joinSession(session.id, {
        userId: "user-2",
        displayName: "Bob",
        channelId: "channel-2",
      });

      const context = manager.buildCollabContext(session.id);

      expect(context).toContain('<collaborative_session name="Test"');
      expect(context).toContain("<participants>");
      expect(context).toContain("Alice");
      expect(context).toContain("Bob");
      expect(context).toContain("Decision mode: owner_decides");
    });

    it("should include expertise in context when present", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner, {});

      const context = manager.buildCollabContext(session.id);

      expect(context).toContain("</collaborative_session>");
    });

    it("should add observer tag for observer roles", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner);
      manager.joinSession(
        session.id,
        {
          userId: "user-2",
          displayName: "Bob",
          channelId: "channel-2",
        },
        "observer",
      );

      const context = manager.buildCollabContext(session.id);

      expect(context).toContain("(observing)");
    });

    it("should add round-robin instruction when enabled", () => {
      const manager = new CollabSessionManager(testWorkspace);
      const session = manager.createSession("Test", "Hooty", mockOwner, {
        roundRobin: true,
      });

      const context = manager.buildCollabContext(session.id);

      expect(context).toContain("round-robin mode");
    });

    it("should return empty string when session not found", () => {
      const manager = new CollabSessionManager(testWorkspace);

      expect(manager.buildCollabContext("nonexistent")).toBe("");
    });
  });

  describe("pruneStale", () => {
    it("should remove sessions older than timeout", () => {
      const manager = new CollabSessionManager(testWorkspace, {
        sessionTimeoutMinutes: 60,
      });
      const session = manager.createSession("Test", "Hooty", mockOwner);

      vi.spyOn(Date, "now").mockReturnValue(
        new Date(session.metadata.lastActivity).getTime() + 61 * 60_000,
      );

      manager.pruneStale();

      expect(manager.getSession(session.id)).toBeNull();
    });

    it("should keep recent sessions", () => {
      const manager = new CollabSessionManager(testWorkspace, {
        sessionTimeoutMinutes: 60,
      });
      const session = manager.createSession("Test", "Hooty", mockOwner);

      manager.pruneStale();

      expect(manager.getSession(session.id)).not.toBeNull();
    });
  });

  describe("loadAll", () => {
    it("should load sessions from disk", () => {
      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readdirSync).mockReturnValue([
        "session-1.json",
      ] as unknown as ReturnType<typeof readdirSync>);

      const storedSession: SharedSession = {
        id: "session-1",
        name: "Loaded Session",
        owlName: "Hooty",
        participants: [],
        messages: [],
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      vi.mocked(readFileSync).mockReturnValue(JSON.stringify(storedSession));

      const manager = new CollabSessionManager(testWorkspace);
      manager.loadAll();

      const sessions = manager.listSessions();
      expect(sessions).toHaveLength(1);
      expect(sessions[0].name).toBe("Loaded Session");
    });

    it("should handle missing sessions directory", () => {
      vi.mocked(existsSync).mockReturnValue(false);

      const manager = new CollabSessionManager(testWorkspace);
      manager.loadAll();

      expect(manager.listSessions()).toHaveLength(0);
    });
  });
});

describe("CollabFacilitator", () => {
  const createMockProvider = (): ModelProvider => ({
    chat: vi.fn(),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
    name: "mock",
  });

  const mockMessages: CollabMessage[] = [
    {
      id: "1",
      userId: "user-1",
      displayName: "Alice",
      role: "user",
      content: "I think we should use TypeScript",
      timestamp: new Date().toISOString(),
    },
    {
      id: "2",
      userId: "user-2",
      displayName: "Bob",
      role: "user",
      content: "I prefer JavaScript",
      timestamp: new Date().toISOString(),
    },
    {
      id: "3",
      userId: "user-1",
      displayName: "Alice",
      role: "user",
      content: "TypeScript has better type safety",
      timestamp: new Date().toISOString(),
    },
    {
      id: "4",
      userId: "user-2",
      displayName: "Bob",
      role: "user",
      content: "But it adds complexity",
      timestamp: new Date().toISOString(),
    },
  ];

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("detectDisagreement", () => {
    it("should detect disagreement when participants disagree", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockResolvedValue({
        content:
          '{"hasDisagreement": true, "participants": ["Alice", "Bob"], "topic": "language choice"}',
        model: "mock",
        finishReason: "stop",
      } as ChatResponse);

      const facilitator = new CollabFacilitator(provider);
      const result = await facilitator.detectDisagreement(mockMessages);

      expect(result?.hasDisagreement).toBe(true);
      expect(result?.participants).toContain("Alice");
      expect(result?.participants).toContain("Bob");
    });

    it("should return null when fewer than 4 messages", async () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const result = await facilitator.detectDisagreement(
        mockMessages.slice(0, 2),
      );

      expect(result).toBeNull();
    });

    it("should return null when only one user", async () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const result = await facilitator.detectDisagreement(
        mockMessages.filter((m) => m.userId === "user-1"),
      );

      expect(result).toBeNull();
    });

    it("should return null when chat response has no JSON", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockResolvedValue({
        content: "No JSON here",
        model: "mock",
        finishReason: "stop",
      } as ChatResponse);

      const facilitator = new CollabFacilitator(provider);
      const result = await facilitator.detectDisagreement(mockMessages);

      expect(result).toBeNull();
    });

    it("should return null when chat throws", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockRejectedValue(new Error("API error"));

      const facilitator = new CollabFacilitator(provider);
      const result = await facilitator.detectDisagreement(mockMessages);

      expect(result).toBeNull();
    });

    it("should use last 10 messages for analysis", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockResolvedValue({
        content: '{"hasDisagreement": false, "participants": [], "topic": ""}',
        model: "mock",
        finishReason: "stop",
      } as ChatResponse);

      const manyMessages = Array.from({ length: 20 }, (_, i) => ({
        id: String(i),
        userId: `user-${(i % 2) + 1}`,
        displayName: i % 2 === 0 ? "Alice" : "Bob",
        role: "user" as const,
        content: `Message ${i}`,
        timestamp: new Date().toISOString(),
      }));

      const facilitator = new CollabFacilitator(provider);
      await facilitator.detectDisagreement(manyMessages);

      const chatCall = vi.mocked(provider.chat).mock.calls[0];
      const messagesContent = chatCall[0][1].content as string;
      expect(messagesContent).toContain("Message 19");
      expect(messagesContent).not.toContain("Message 0");
    });
  });

  describe("summarize", () => {
    it("should generate session summary", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockResolvedValue({
        content:
          "Key points: TypeScript vs JavaScript debate. No decision made.",
        model: "mock",
        finishReason: "stop",
      } as ChatResponse);

      const facilitator = new CollabFacilitator(provider);
      const session: SharedSession = {
        id: "session-1",
        name: "Language Debate",
        owlName: "Hooty",
        participants: [
          {
            userId: "user-1",
            displayName: "Alice",
            role: "owner",
            joinedAt: new Date().toISOString(),
            lastActiveAt: new Date().toISOString(),
            channelId: "channel-1",
          },
        ],
        messages: mockMessages,
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      const summary = await facilitator.summarize(session);

      expect(summary).toBe(
        "Key points: TypeScript vs JavaScript debate. No decision made.",
      );
      expect(provider.chat).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ role: "system" }),
          expect.objectContaining({ role: "user" }),
        ]),
        undefined,
        expect.objectContaining({ temperature: 0.3, maxTokens: 500 }),
      );
    });

    it("should return failure message when chat throws", async () => {
      const provider = createMockProvider();
      vi.mocked(provider.chat).mockRejectedValue(new Error("API error"));

      const facilitator = new CollabFacilitator(provider);
      const session: SharedSession = {
        id: "session-1",
        name: "Test",
        owlName: "Hooty",
        participants: [],
        messages: [],
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      const summary = await facilitator.summarize(session);

      expect(summary).toBe("Summary generation failed.");
    });
  });

  describe("formatDecisionPrompt", () => {
    it("should format prompt for consensus mode", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const prompt = facilitator.formatDecisionPrompt(
        "Which language?",
        [
          { userId: "user-1", displayName: "Alice", position: "TypeScript" },
          { userId: "user-2", displayName: "Bob", position: "JavaScript" },
        ],
        "consensus",
      );

      expect(prompt).toContain("Which language?");
      expect(prompt).toContain("**Alice**: TypeScript");
      expect(prompt).toContain("**Bob**: JavaScript");
      expect(prompt).toContain("All members must agree");
    });

    it("should format prompt for majority mode", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const prompt = facilitator.formatDecisionPrompt(
        "Which framework?",
        [{ userId: "user-1", displayName: "Alice", position: "React" }],
        "majority",
      );

      expect(prompt).toContain("majority vote");
    });

    it("should format prompt for owner_decides mode", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const prompt = facilitator.formatDecisionPrompt(
        "Deployment strategy",
        [{ userId: "user-1", displayName: "Alice", position: "Docker" }],
        "owner_decides",
      );

      expect(prompt).toContain("session owner will make the final call");
    });
  });

  describe("toEngineMessages", () => {
    it("should convert session messages to chat messages for current user", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const session: SharedSession = {
        id: "session-1",
        name: "Test",
        owlName: "Hooty",
        participants: [
          {
            userId: "user-1",
            displayName: "Alice",
            role: "owner",
            joinedAt: new Date().toISOString(),
            lastActiveAt: new Date().toISOString(),
            channelId: "channel-1",
          },
        ],
        messages: mockMessages,
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      const messages = facilitator.toEngineMessages(session, "user-1");

      expect(messages).toHaveLength(4);
      expect(messages[0]).toEqual({
        role: "user",
        content: "I think we should use TypeScript",
      });
      expect(messages[1]).toEqual({
        role: "user",
        content: "[Bob]: I prefer JavaScript",
      });
    });

    it("should prefix other users messages with their display name", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const session: SharedSession = {
        id: "session-1",
        name: "Test",
        owlName: "Hooty",
        participants: [],
        messages: [
          {
            id: "1",
            userId: "user-2",
            displayName: "Bob",
            role: "user",
            content: "Hello!",
            timestamp: new Date().toISOString(),
          },
        ],
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      const messages = facilitator.toEngineMessages(session, "user-1");

      expect(messages[0]).toEqual({ role: "user", content: "[Bob]: Hello!" });
    });

    it("should handle assistant messages correctly", () => {
      const facilitator = new CollabFacilitator(createMockProvider());
      const session: SharedSession = {
        id: "session-1",
        name: "Test",
        owlName: "Hooty",
        participants: [],
        messages: [
          {
            id: "1",
            userId: "assistant",
            displayName: "Owl",
            role: "assistant",
            content: "I have thoughts on this",
            timestamp: new Date().toISOString(),
          },
        ],
        metadata: {
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
        },
        settings: {
          maxParticipants: 10,
          allowObservers: true,
          roundRobin: false,
          decisionMode: "owner_decides",
          autoSummarize: true,
        },
      };

      const messages = facilitator.toEngineMessages(session, "user-1");

      expect(messages[0]).toEqual({
        role: "assistant",
        content: "I have thoughts on this",
      });
    });
  });
});

describe("CollabConfig", () => {
  it("should use default config values", () => {
    const manager = new CollabSessionManager("/test", {});

    const session = manager.createSession("Test", "Hooty", {
      userId: "user-1",
      displayName: "Alice",
      channelId: "channel-1",
    });

    expect(session.settings.maxParticipants).toBe(10);
    expect(session.settings.allowObservers).toBe(true);
    expect(session.settings.roundRobin).toBe(false);
    expect(session.settings.decisionMode).toBe("owner_decides");
    expect(session.settings.autoSummarize).toBe(true);
  });

  it("should merge partial config", () => {
    const manager = new CollabSessionManager("/test", {
      maxActiveSessions: 3,
      sessionTimeoutMinutes: 60,
    });

    const session = manager.createSession("Test", "Hooty", {
      userId: "user-1",
      displayName: "Alice",
      channelId: "channel-1",
    });

    expect(session.settings.maxParticipants).toBe(10);
  });
});
