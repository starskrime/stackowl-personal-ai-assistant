import { describe, it, expect } from "vitest";
import { CALLBACK_PREFIX, TELEGRAM_LIMITS } from "../../../src/gateway/adapters/telegram/constants.js";

describe("CALLBACK_PREFIX", () => {
  it("exports all five known callback prefixes", () => {
    expect(CALLBACK_PREFIX.NAV).toBe("nav:");
    expect(CALLBACK_PREFIX.CFG).toBe("cfg:");
    expect(CALLBACK_PREFIX.VCFG).toBe("vcfg:");
    expect(CALLBACK_PREFIX.WIZ).toBe("wiz:");
    expect(CALLBACK_PREFIX.FB).toBe("fb:");
  });
});

describe("TELEGRAM_LIMITS", () => {
  it("exports message length limits", () => {
    expect(TELEGRAM_LIMITS.MAX_MESSAGE_LENGTH).toBe(4096);
    expect(TELEGRAM_LIMITS.CHUNK_LENGTH).toBe(3800);
    expect(TELEGRAM_LIMITS.MAX_CHUNKS).toBe(5);
    expect(TELEGRAM_LIMITS.BOT_MENU_DESCRIPTION_MAX).toBe(256);
    expect(TELEGRAM_LIMITS.BOT_MENU_DESCRIPTION_TRUNCATE).toBe(253);
    expect(TELEGRAM_LIMITS.STREAM_FLUSH_INTERVAL_MS).toBe(500);
    expect(TELEGRAM_LIMITS.MAX_EDIT_FAILURES).toBe(3);
    expect(TELEGRAM_LIMITS.STREAM_THROTTLE_MS).toBe(1000);
  });
});
