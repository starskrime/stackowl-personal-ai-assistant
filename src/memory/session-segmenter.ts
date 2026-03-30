/**
 * StackOwl — Session Segmenter
 *
 * Breaks unbounded Telegram sessions into logical segments based on
 * temporal gaps and continuity classification.
 *
 * A new segment starts when:
 *   - Gap between consecutive messages > 30 minutes
 *   - ContinuityEngine classified TOPIC_SWITCH or FRESH_START
 *
 * On segment boundary:
 *   1. Extract episode from completed segment
 *   2. Save to episodic memory
 *   3. New messages continue in same session file (backward compat)
 */

import type { ChatMessage } from "../providers/base.js";
import type { Session } from "./store.js";

export interface SessionSegment {
  startIndex: number;
  endIndex: number;
  startedAt: number;
  endedAt: number;
  messageCount: number;
}

const SEGMENT_GAP_MS = 30 * 60 * 1000; // 30 minutes

/**
 * Find segment boundaries in a session based on temporal gaps.
 * Returns an array of segments. The last segment is the "current" one.
 */
export function findSegments(session: Session): SessionSegment[] {
  const messages = session.messages;
  if (messages.length === 0) return [];

  const segments: SessionSegment[] = [];
  let segStart = 0;
  let prevTimestamp = getMessageTimestamp(messages[0], session.metadata.startedAt);

  for (let i = 1; i < messages.length; i++) {
    const ts = getMessageTimestamp(messages[i], prevTimestamp + 1000);
    const gap = ts - prevTimestamp;

    if (gap > SEGMENT_GAP_MS) {
      // Close previous segment
      segments.push({
        startIndex: segStart,
        endIndex: i - 1,
        startedAt: getMessageTimestamp(messages[segStart], session.metadata.startedAt),
        endedAt: prevTimestamp,
        messageCount: i - segStart,
      });
      segStart = i;
    }
    prevTimestamp = ts;
  }

  // Current (open) segment
  segments.push({
    startIndex: segStart,
    endIndex: messages.length - 1,
    startedAt: getMessageTimestamp(messages[segStart], session.metadata.startedAt),
    endedAt: prevTimestamp,
    messageCount: messages.length - segStart,
  });

  return segments;
}

/**
 * Get messages belonging to a specific segment.
 */
export function getSegmentMessages(
  session: Session,
  segment: SessionSegment,
): ChatMessage[] {
  return session.messages.slice(segment.startIndex, segment.endIndex + 1);
}

/**
 * Check if there's a completed segment that hasn't been extracted yet.
 * A segment is "completed" if there's a 30min+ gap after it and
 * it's not the current (last) segment.
 *
 * Returns the completed segments that need episode extraction.
 */
export function getUnextractedSegments(
  session: Session,
  extractedUpToIndex: number,
): SessionSegment[] {
  const segments = findSegments(session);
  if (segments.length <= 1) return []; // Only current segment, nothing to extract

  // All segments except the last (current) one, starting after extractedUpToIndex
  return segments.slice(0, -1).filter(
    (seg) => seg.startIndex >= extractedUpToIndex,
  );
}

function getMessageTimestamp(msg: ChatMessage, fallback: number): number {
  // Messages may have a timestamp property added by channels
  if ((msg as any).timestamp) {
    const ts = new Date((msg as any).timestamp).getTime();
    if (!isNaN(ts)) return ts;
  }
  return fallback;
}
