/**
 * ACP subsystem has been replaced by the A2A subsystem.
 * All protocol tests are covered in __tests__/a2a.test.ts (14 scenarios).
 * This file is retained as a placeholder to avoid stale test runner errors.
 */
import { describe, it } from "vitest";

describe("ACP (migrated to A2A)", () => {
  it("is superseded by __tests__/a2a.test.ts", () => {
    // All ACP behavior has been migrated to A2ARegistry.
    // See src/a2a/ for the replacement implementation.
  });
});
