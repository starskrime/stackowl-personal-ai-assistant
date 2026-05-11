import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import Database from "better-sqlite3";
import { PairingService } from "../../src/gateway/security/pairing.js";

const DB_PATH = join(tmpdir(), `stackowl-pairing-test-${Date.now()}.db`);
let db: InstanceType<typeof Database>;
let service: PairingService;

beforeEach(() => {
  db = new Database(DB_PATH);
  service = new PairingService(db);
});

afterEach(async () => {
  db.close();
  await rm(DB_PATH, { force: true });
});

describe("PairingService", () => {
  it("issues a challenge code for an unknown sender", () => {
    const code = service.challenge("discord", "user123");
    expect(code).toMatch(/^[A-Z0-9]{6}$/);
  });

  it("returns same pending code for same sender on repeated challenge", () => {
    const c1 = service.challenge("discord", "user123");
    const c2 = service.challenge("discord", "user123");
    expect(c1).toBe(c2);
  });

  it("approves a valid code and authorizes the sender", () => {
    const code = service.challenge("discord", "user123");
    const ok = service.approve("discord", "user123", code);
    expect(ok).toBe(true);
    expect(service.isAuthorized("discord", "user123")).toBe(true);
  });

  it("rejects an incorrect code", () => {
    service.challenge("discord", "user123");
    const ok = service.approve("discord", "user123", "WRONG1");
    expect(ok).toBe(false);
    expect(service.isAuthorized("discord", "user123")).toBe(false);
  });

  it("treats unknown senders as not authorized", () => {
    expect(service.isAuthorized("discord", "unknown_user")).toBe(false);
  });
});
