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

  it("idle state: renders footer with owl name and model", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("Hoots");
    expect(lastFrame()).toContain("sonnet-4-6");
  });

  it("generating state: shows generating text instead of ❯", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("generating...");
    expect(lastFrame()).not.toContain("❯");
  });

  it("generating state: footer shows esc esc to stop when generating=true in store", () => {
    uiStore.setState({ generating: true });
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("esc esc to stop");
  });

  it("footer omits tokens and cost when both are zero", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).not.toContain("tok");
    expect(lastFrame()).not.toContain("$");
  });

  it("footer shows tokens and cost when non-zero", () => {
    uiStore.setState({ totalTokens: 1234, totalCostUsd: 0.0023 });
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("1,234 tok");
    expect(lastFrame()).toContain("$0.0023");
  });
});
