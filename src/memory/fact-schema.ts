import { z } from "zod";

// ─── Fact Type ────────────────────────────────────────────────────

export const FactTypeSchema = z.enum([
  "user_preference",    // Q1: How does the user want to be spoken to?
  "dream_reflection",   // Q2: Has the owl made this mistake before, and what was the fix?
  "approach_confirmed", // Q3: What tools/approaches have worked for this task?
  "approach_failed",    // Q3: What tools/approaches have failed for this task?
  "project_context",    // Q4: What is the user currently building and what are active constraints?
  "owl_calibration",    // Q5: How should this owl persona behave with this user?
]);

export type FactType = z.infer<typeof FactTypeSchema>;

// ─── Core Fact ────────────────────────────────────────────────────

export const FactSchema = z.object({
  factId: z.string().uuid(),
  type: FactTypeSchema,
  content: z.string().min(1).max(2000),
  confidence: z.number().min(0).max(1),
  source: z.string(),             // sessionId the fact was extracted from
  confirmationCount: z.number().int().min(0).default(0),
  contradictions: z.array(z.string()).default([]), // factIds that contradict this fact
  owlName: z.string(),
  userId: z.string(),
  createdAt: z.string().datetime(),
});

export type Fact = z.infer<typeof FactSchema>;

// ─── Fact Diff — output of FactExtractor ─────────────────────────

export const ContradictionSchema = z.object({
  existingFactId: z.string().uuid(),
  newContent: z.string().min(1).max(2000),
  reason: z.string(),
});

export const FactDiffSchema = z.object({
  new: z.array(FactSchema),
  updated: z.array(FactSchema),
  contradictions: z.array(ContradictionSchema),
});

export type FactDiff = z.infer<typeof FactDiffSchema>;
export type Contradiction = z.infer<typeof ContradictionSchema>;

// ─── IPC Message Protocol ─────────────────────────────────────────
// Discriminated unions — no other shapes are valid.

export type ChatMessage = { role: string; content: string };

// Main → MemoryWorker
export type MainToMemory =
  | { type: "extract"; sessionId: string; messages: ChatMessage[]; owlName: string; userId: string }
  | { type: "search"; query: string; topK: number; requestId: string }
  | { type: "shutdown" };

// MemoryWorker → Main
export type MemoryToMain =
  | { type: "search-result"; requestId: string; facts: Fact[] }
  | { type: "extract-done"; factCount: number }
  | { type: "error"; message: string };

// DreamWorker → MemoryWorker (write requests — Actor Model)
export type DreamToMemory =
  | { type: "write-fact"; fact: Fact; requestId: string }
  | { type: "delete-fact"; factId: string; requestId: string };

// MemoryWorker → DreamWorker (acks)
export type MemoryToDream =
  | { type: "write-ack"; requestId: string; success: boolean };
