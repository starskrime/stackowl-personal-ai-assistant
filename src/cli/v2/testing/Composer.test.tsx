import { describe, it, expect, beforeEach } from "vitest";
import { render } from "ink-testing-library";
import { UiStoreProvider } from "../providers/UiStoreProvider.js";
import { CommandDispatcherProvider } from "../providers/CommandDispatcherProvider.js";
import { uiStore } from "../state/store.js";
import { Composer } from "../components/Composer.js";
import type { CommandDispatcher } from "../commands/dispatcher.js";

const noopDispatcher: CommandDispatcher = {
  dispatch: async () => ({ kind: "action" }),
};

function ComposerUnderTest({ disabled }: { disabled: boolean }) {
  return (
    <CommandDispatcherProvider dispatcher={noopDispatcher}>
      <UiStoreProvider>
        <Composer onSubmit={() => {}} disabled={disabled} />
      </UiStoreProvider>
    </CommandDispatcherProvider>
  );
}

beforeEach(() => {
  uiStore.setState({
    generating: false,
    activeOwlName: "Hoots",
    activeOwlEmoji: "🦉",
    activeModel: "sonnet-4-6",
    totalTokens: 0,
    totalCostUsd: 0,
  });
});

describe("Composer", () => {
  it("idle state: renders ❯ prompt and cursor", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("❯");
    expect(lastFrame()).toContain("▋");
  });

  it("idle state: renders input row with caret and cursor", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("❯");
    expect(lastFrame()).toContain("▋");
  });

  it("idle state: does not render slash hint row", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).not.toContain("/help");
  });

  it("generating state: shows dimmed input row, no cursor", () => {
    uiStore.setState({ generating: true });
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("❯");
    expect(lastFrame()).not.toContain("generating...");
    expect(lastFrame()).not.toContain("▋");
  });
});
