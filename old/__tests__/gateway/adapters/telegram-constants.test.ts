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

import { REGISTRY } from "../../../src/cli/v2/commands/registry.js";
import type { CommandSpec } from "../../../src/cli/v2/commands/registry.js";

describe("CommandSpec Telegram extensions", () => {
  it("CommandSpec accepts telegramVisible, telegramDescription, telegramSpecialCase", () => {
    const spec: CommandSpec = {
      name: "/test",
      description: "test",
      handler: async () => ({ kind: "action" }),
      telegramVisible: false,
      telegramDescription: "Short desc",
      telegramSpecialCase: true,
    };
    expect(spec.telegramVisible).toBe(false);
    expect(spec.telegramDescription).toBe("Short desc");
    expect(spec.telegramSpecialCase).toBe(true);
  });

  it("REGISTRY /config entry has telegramSpecialCase set", () => {
    const configSpec = REGISTRY.find(s => s.name === "/config");
    expect(configSpec).toBeDefined();
    expect(configSpec!.telegramSpecialCase).toBe(true);
  });

  it("REGISTRY /quit entry has telegramVisible: false", () => {
    const quitSpec = REGISTRY.find(s => s.name === "/quit");
    expect(quitSpec).toBeDefined();
    expect(quitSpec!.telegramVisible).toBe(false);
  });

  it("REGISTRY /onboarding entry has telegramVisible: false", () => {
    const onboardingSpec = REGISTRY.find(s => s.name === "/onboarding");
    expect(onboardingSpec).toBeDefined();
    expect(onboardingSpec!.telegramVisible).toBe(false);
  });
});
