import { describe, it, expect } from "vitest";
import { ProcessImpl } from "../../src/platform/capabilities/process.js";

const proc = new ProcessImpl();

describe("ProcessImpl", () => {
  it("currentInfo() returns this process's pid", () => {
    expect(proc.currentInfo().pid).toBe(process.pid);
  });

  it("isAlive(process.pid) returns true", () => {
    expect(proc.isAlive(process.pid)).toBe(true);
  });

  it("isAlive(99999999) returns false", () => {
    expect(proc.isAlive(99999999)).toBe(false);
  });

  it("list() includes the current process", async () => {
    const list = await proc.list();
    expect(list.some((p) => p.pid === process.pid)).toBe(true);
  });

  it("list({ pid }) filters to a single process", async () => {
    const list = await proc.list({ pid: process.pid });
    expect(list).toHaveLength(1);
    expect(list[0].pid).toBe(process.pid);
  });

  it("kill(non-existent pid) returns false (no throw)", async () => {
    const result = await proc.kill(99999999);
    expect(result).toBe(false);
  });
});
