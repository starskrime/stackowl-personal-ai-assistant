/**
 * StackOwl — Storage Abstraction
 *
 * Backend-agnostic interfaces for session and pellet persistence.
 * Implementations: file-based (default) and SQLite (opt-in).
 */

import type { Session } from "../memory/store.js";

// ─── Session Storage ────────────────────────────────────────────

export interface SessionListOptions {
  owlName?: string;
  limit?: number;
  offset?: number;
  /** Only sessions updated after this timestamp */
  since?: number;
}

export interface SessionStorage {
  init(): Promise<void>;
  save(session: Session): Promise<void>;
  load(id: string): Promise<Session | null>;
  list(opts?: SessionListOptions): Promise<Session[]>;
  delete(id: string): Promise<boolean>;
  /** Count total sessions without loading them all */
  count(): Promise<number>;
}

// ─── Pellet Storage ─────────────────────────────────────────────

export interface PelletRecord {
  id: string;
  title: string;
  content: string;
  tags: string[];
  owls: string[];
  source: string;
  generatedAt: string;
  version: number;
}

export interface PelletListOptions {
  limit?: number;
  offset?: number;
  tag?: string;
}

export interface PelletStorage {
  init(): Promise<void>;
  save(pellet: PelletRecord): Promise<void>;
  get(id: string): Promise<PelletRecord | null>;
  list(opts?: PelletListOptions): Promise<PelletRecord[]>;
  delete(id: string): Promise<boolean>;
  count(): Promise<number>;
}

// ─── Storage Factory ────────────────────────────────────────────

export type StorageBackend = "file" | "sqlite";

export interface StorageConfig {
  backend: StorageBackend;
  /** Path to SQLite database file (only for sqlite backend) */
  sqlitePath?: string;
}
