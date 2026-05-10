import { describe, it, expect, beforeEach } from "vitest";
import { render } from "ink-testing-library";
import { UiStoreProvider } from "../providers/UiStoreProvider.js";
import { uiStore } from "../state/store.js";
import { Composer } from "../components/Composer.js";

function ComposerUnderTest({ disabled }: { disabled: boolean }) {
  return (
    <UiStoreProvider>
      <Composer onSubmit={() => {}} disabled={disabled} />
    </UiStoreProvider>
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

  it("idle state: renders bordered box (╭ and ╰)", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("╭");
    expect(lastFrame()).toContain("╰");
  });

  it("idle state: renders slash hint row when value is empty", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("/help");
  });

  it("generating state: shows generating text instead of ❯", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("generating...");
    expect(lastFrame()).not.toContain("❯");
  });
});
