/**
 * StackOwl — Memory Threads Types
 *
 * Data structures for conversational recall and thread reconstruction.
 */

export interface MemoryThread {
  /** Unique thread ID */
  id: string;
  /** The user's recall query */
  query: string;
  /** Timeline of related entries sorted chronologically */
  timeline: ThreadEntry[];
  /** Related pellet IDs */
  relatedPellets: string[];
  /** Related session IDs */
  relatedSessions: string[];
  /** LLM-generated narrative summary */
  narrative: string;
  /** When this thread was reconstructed */
  reconstructedAt: string;
}

export interface ThreadEntry {
  /** When this entry occurred */
  timestamp: string;
  /** Source type */
  source: 'session' | 'pellet' | 'memory';
  /** Source identifier (session ID or pellet ID) */
  sourceId: string;
  /** Relevant text excerpt */
  excerpt: string;
  /** BM25 or keyword relevance score */
  relevanceScore: number;
}

export interface SessionIndexEntry {
  /** Session ID */
  sessionId: string;
  /** Extracted topic keywords */
  topics: string[];
  /** Session start timestamp */
  timestamp: number;
  /** Message count */
  messageCount: number;
  /** First user message (for context) */
  firstMessage: string;
}

export interface SessionIndex {
  entries: SessionIndexEntry[];
  lastUpdated: string;
}
