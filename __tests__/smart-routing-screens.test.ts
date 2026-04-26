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
