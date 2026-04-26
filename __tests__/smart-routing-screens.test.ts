import { describe, it, expect } from "vitest";
import { MenuStateManager } from "../src/gateway/adapters/telegram-config/state.js";

describe("MenuStateManager — smart routing screens", () => {
  it("can navigate to smart_routing screen", () => {
    const mgr = new MenuStateManager();
    mgr.set({
      userId: 1, chatId: 1, messageId: 1,
      screen: "main", breadcrumb: [], lastActivity: Date.now(),
    });
    mgr.navigate(1, "smart_routing");
    expect(mgr.get(1)?.screen).toBe("smart_routing");
  });

  it("can navigate to sr_prov_pick screen", () => {
    const mgr = new MenuStateManager();
    mgr.set({
      userId: 2, chatId: 2, messageId: 2,
      screen: "main", breadcrumb: [], lastActivity: Date.now(),
    });
    mgr.navigate(2, "sr_prov_pick");
    expect(mgr.get(2)?.screen).toBe("sr_prov_pick");
  });
});

import {
  renderSmartRouting,
  renderSmartRoutingProviderPicker,
  renderSmartRoutingModelPicker,
} from "../src/gateway/adapters/telegram-config/screens.js";
import type { StackOwlConfig } from "../src/config/loader.js";

function baseConfig(overrides?: Partial<NonNullable<StackOwlConfig["smartRouting"]>>): StackOwlConfig {
  return {
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    providers: {},
    smartRouting: {
      enabled: false,
      availableModels: [],
      ...overrides,
    },
  } as unknown as StackOwlConfig;
}

describe("renderSmartRouting", () => {
  it("shows toggle button", () => {
    const { text, keyboard } = renderSmartRouting(baseConfig());
    expect(text).toContain("Smart Routing");
    const buttons = keyboard.inline_keyboard.flat().map(b => b.text);
    expect(buttons.some(b => b.includes("Enable") || b.includes("Disable"))).toBe(true);
  });

  it("shows roster entries in text", () => {
    const config = baseConfig({
      enabled: true,
      availableModels: [
        { modelName: "llama3.2", providerName: "ollama" },
        { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
      ],
    });
    const { text } = renderSmartRouting(config);
    expect(text).toContain("ollama");
    expect(text).toContain("anthropic");
    expect(text).toContain("llama3.2");
    expect(text).toContain("claude-sonnet-4-6");
  });

  it("shows Add Model button", () => {
    const config = baseConfig({ enabled: true, availableModels: [] });
    const { keyboard } = renderSmartRouting(config);
    const buttons = keyboard.inline_keyboard.flat().map(b => b.text);
    expect(buttons.some(b => b.includes("Add"))).toBe(true);
  });

  it("first entry up button is noop", () => {
    const config = baseConfig({
      enabled: true,
      availableModels: [
        { modelName: "llama3.2", providerName: "ollama" },
        { modelName: "gpt-4o", providerName: "openai" },
      ],
    });
    const { keyboard } = renderSmartRouting(config);
    const allButtons = keyboard.inline_keyboard.flat();
    // First entry row: up button should be noop
    const noopUp = allButtons.find(b => (b as any).callback_data === "cfg:noop" && b.text === "·");
    expect(noopUp).toBeDefined();
  });
});

describe("renderSmartRoutingProviderPicker", () => {
  it("shows all provider keys as buttons", () => {
    const { keyboard } = renderSmartRoutingProviderPicker(["ollama", "anthropic"]);
    const callbacks = keyboard.inline_keyboard.flat().map(b => (b as any).callback_data as string);
    expect(callbacks).toContain("cfg:sr_ap:ollama");
    expect(callbacks).toContain("cfg:sr_ap:anthropic");
  });
});

describe("renderSmartRoutingModelPicker", () => {
  it("shows model buttons with provider prefix in callback", () => {
    const { keyboard } = renderSmartRoutingModelPicker("ollama", ["llama3.2", "mistral"]);
    const callbacks = keyboard.inline_keyboard.flat().map(b => (b as any).callback_data as string);
    expect(callbacks).toContain("cfg:sr_am:ollama:llama3.2");
    expect(callbacks).toContain("cfg:sr_am:ollama:mistral");
  });
});
