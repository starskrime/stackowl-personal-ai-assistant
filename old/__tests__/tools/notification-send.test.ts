import { describe, it, expect, vi } from "vitest";
import { createPlatform } from "../../src/platform/index.js";
import { createNotificationSendTool } from "../../src/tools/notification-send.js";

describe("NotificationSendTool", () => {
  it("delivers via platform.notifier", async () => {
    const captured: any[] = [];
    const platform = createPlatform({
      notifier: {
        nativeImpl: {
          notify: (opts: any, cb: any) => {
            captured.push(opts);
            cb(null, "ok");
          },
        },
      },
    } as any);
    const tool = createNotificationSendTool(platform);
    const res = await tool.execute(
      { title: "Hi", body: "test" },
      { cwd: "/tmp", engineContext: { sessionId: "s1" } } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.via).toBe("native");
    expect(captured.length).toBe(1);
  });

  it("rate-limits at 10 per minute per session", async () => {
    const platform = createPlatform({
      notifier: {
        nativeImpl: {
          notify: (_o: any, cb: any) => cb(null, "ok"),
        },
      },
    } as any);
    const tool = createNotificationSendTool(platform);
    const ctx = { cwd: "/tmp", engineContext: { sessionId: "rate-test" } } as any;
    for (let i = 0; i < 10; i++) {
      await tool.execute({ title: `n${i}`, body: "x" }, ctx);
    }
    const res = await tool.execute({ title: "n11", body: "x" }, ctx);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("E_RATE_LIMITED");
  });

  it("rate-limit is per-session (different sessions get fresh budget)", async () => {
    const platform = createPlatform({
      notifier: {
        nativeImpl: {
          notify: (_o: any, cb: any) => cb(null, "ok"),
        },
      },
    } as any);
    const tool = createNotificationSendTool(platform);
    for (let i = 0; i < 10; i++) {
      await tool.execute({ title: `a${i}`, body: "x" }, {
        cwd: "/tmp",
        engineContext: { sessionId: "sess-A" },
      } as any);
    }
    const res = await tool.execute({ title: "b", body: "x" }, {
      cwd: "/tmp",
      engineContext: { sessionId: "sess-B" },
    } as any);
    expect(JSON.parse(res).success).toBe(true);
  });
});
