import { describe, it, expect } from "vitest";
import { applyPanelEvent } from "../../../../src/cli/v2/state/slices/panel.js";
import type { UiState } from "../../../../src/cli/v2/state/store.js";

const baseState = (): UiState => ({
  activePanel: null,
  panelFocus: "composer",
} as unknown as UiState);

describe("applyPanelEvent", () => {
  it("opens a panel and sets focus to panel", () => {
    const state = baseState();
    const next = applyPanelEvent(state, {
      kind: "panel.opened",
      id: "skills",
      props: { title: "Skills", items: [] },
    });
    expect(next.activePanel).toEqual({ id: "skills", props: { title: "Skills", items: [] } });
    expect(next.panelFocus).toBe("panel");
  });

  it("closes a panel and returns focus to composer", () => {
    const state = { ...baseState(), activePanel: { id: "skills", props: {} }, panelFocus: "panel" as const };
    const next = applyPanelEvent(state as unknown as UiState, { kind: "panel.closed" });
    expect(next.activePanel).toBeNull();
    expect(next.panelFocus).toBe("composer");
  });

  it("returns state unchanged for unrelated events", () => {
    const state = baseState();
    const next = applyPanelEvent(state, { kind: "token.delta", turnId: "t1", text: "hi" } as any);
    expect(next).toBe(state);
  });

  it("opening a second panel replaces the first", () => {
    let state = baseState();
    state = applyPanelEvent(state, { kind: "panel.opened", id: "skills", props: {} });
    state = applyPanelEvent(state, { kind: "panel.opened", id: "memory", props: {} });
    expect(state.activePanel!.id).toBe("memory");
    expect(state.panelFocus).toBe("panel");
  });

  it("closing a panel that was not open is a no-op", () => {
    const state = baseState();
    const next = applyPanelEvent(state, { kind: "panel.closed" });
    expect(next.activePanel).toBeNull();
    expect(next.panelFocus).toBe("composer");
  });
});
