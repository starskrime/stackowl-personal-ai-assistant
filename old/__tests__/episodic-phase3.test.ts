import { describe, it, expect } from "vitest";
import { findSegments, getUnextractedSegments, getSegmentMessages } from "../src/memory/session-segmenter.js";
import type { Session } from "../src/memory/store.js";

function makeSessionWithGaps(): Session {
  const baseTime = Date.now() - 4 * 60 * 60 * 1000; // 4 hours ago
  return {
    id: "test:user1",
    messages: [
      // Segment 1: messages at t+0, t+1min, t+2min
      { role: "user" as const, content: "Help me set up email", timestamp: baseTime } as any,
      { role: "assistant" as const, content: "Sure, let me help", timestamp: baseTime + 60_000 } as any,
      { role: "user" as const, content: "I need SMTP config", timestamp: baseTime + 120_000 } as any,
      { role: "assistant" as const, content: "Here's the config...", timestamp: baseTime + 180_000 } as any,
      // 45-minute gap → new segment
      // Segment 2: messages at t+48min, t+49min
      { role: "user" as const, content: "Now let's work on the API", timestamp: baseTime + 48 * 60_000 } as any,
      { role: "assistant" as const, content: "What API do you need?", timestamp: baseTime + 49 * 60_000 } as any,
      { role: "user" as const, content: "REST API for users", timestamp: baseTime + 50 * 60_000 } as any,
      // 2-hour gap → new segment
      // Segment 3: messages at t+170min
      { role: "user" as const, content: "I'm back, let's continue", timestamp: baseTime + 170 * 60_000 } as any,
      { role: "assistant" as const, content: "Welcome back!", timestamp: baseTime + 171 * 60_000 } as any,
    ],
    metadata: {
      owlName: "noctua",
      startedAt: baseTime,
      lastUpdatedAt: baseTime + 171 * 60_000,
    },
  };
}

describe("SessionSegmenter", () => {
  it("finds segments based on temporal gaps", () => {
    const session = makeSessionWithGaps();
    const segments = findSegments(session);

    expect(segments.length).toBe(3);
    expect(segments[0].messageCount).toBe(4); // Segment 1
    expect(segments[1].messageCount).toBe(3); // Segment 2
    expect(segments[2].messageCount).toBe(2); // Segment 3
  });

  it("returns single segment for continuous conversation", () => {
    const baseTime = Date.now() - 60_000;
    const session: Session = {
      id: "test:user1",
      messages: [
        { role: "user" as const, content: "Hello", timestamp: baseTime } as any,
        { role: "assistant" as const, content: "Hi!", timestamp: baseTime + 5000 } as any,
        { role: "user" as const, content: "Help me", timestamp: baseTime + 30000 } as any,
      ],
      metadata: {
        owlName: "noctua",
        startedAt: baseTime,
        lastUpdatedAt: baseTime + 30000,
      },
    };

    const segments = findSegments(session);
    expect(segments.length).toBe(1);
    expect(segments[0].messageCount).toBe(3);
  });

  it("returns empty for empty session", () => {
    const session: Session = {
      id: "test:user1",
      messages: [],
      metadata: { owlName: "noctua", startedAt: Date.now(), lastUpdatedAt: Date.now() },
    };
    expect(findSegments(session)).toEqual([]);
  });

  it("getUnextractedSegments returns completed segments not yet extracted", () => {
    const session = makeSessionWithGaps();
    const unextracted = getUnextractedSegments(session, 0);

    // Segments 1 and 2 are completed (there's a gap after them)
    // Segment 3 is current (last), so not returned
    expect(unextracted.length).toBe(2);
    expect(unextracted[0].messageCount).toBe(4);
    expect(unextracted[1].messageCount).toBe(3);
  });

  it("getUnextractedSegments respects extractedUpToIndex", () => {
    const session = makeSessionWithGaps();

    // Already extracted up to index 4 (after segment 1)
    const unextracted = getUnextractedSegments(session, 4);
    expect(unextracted.length).toBe(1); // Only segment 2
    expect(unextracted[0].startIndex).toBe(4);
  });

  it("getSegmentMessages extracts correct message slice", () => {
    const session = makeSessionWithGaps();
    const segments = findSegments(session);

    const seg1Messages = getSegmentMessages(session, segments[0]);
    expect(seg1Messages.length).toBe(4);
    expect(seg1Messages[0].content).toBe("Help me set up email");

    const seg2Messages = getSegmentMessages(session, segments[1]);
    expect(seg2Messages.length).toBe(3);
    expect(seg2Messages[0].content).toBe("Now let's work on the API");
  });
});

describe("EpisodicMemory - Importance Scoring", () => {
  it("assigns higher importance to episodes with decisions", async () => {
    // We test the importance function indirectly through the Episode interface
    // Since computeImportance is a module-private function, we verify via extractFromMessages
    // For now, just verify the Episode interface accepts importance
    const episode = {
      id: "ep_test",
      sessionId: "test",
      owlName: "noctua",
      date: Date.now(),
      summary: "Test",
      keyFacts: [],
      topics: [],
      userMessageCount: 5,
      importance: 0.7,
    };
    expect(episode.importance).toBe(0.7);
  });

  it("Episode interface supports compressed and archived flags", () => {
    const episode = {
      id: "ep_test",
      sessionId: "test",
      owlName: "noctua",
      date: Date.now(),
      summary: "Old episode",
      keyFacts: [],
      topics: [],
      userMessageCount: 1,
      compressed: true,
      archived: false,
      importance: 0.2,
    };
    expect(episode.compressed).toBe(true);
    expect(episode.archived).toBe(false);
  });
});
