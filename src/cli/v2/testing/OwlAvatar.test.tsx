import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { OwlAvatar } from "../components/OwlAvatar.js";

describe("OwlAvatar", () => {
  it("renders emoji, name, and role", () => {
    const { lastFrame } = render(
      <OwlAvatar emoji="🦉" name="Hoots" role="strategist" />
    );
    expect(lastFrame()).toContain("🦉");
    expect(lastFrame()).toContain("Hoots");
    expect(lastFrame()).toContain("strategist");
  });

  it("renders without role when omitted", () => {
    const { lastFrame } = render(<OwlAvatar emoji="🦉" name="Hoots" />);
    expect(lastFrame()).toContain("🦉");
    expect(lastFrame()).toContain("Hoots");
  });

  it("accepts a custom color override", () => {
    // Just verifies it renders without error when color is passed
    const { lastFrame } = render(
      <OwlAvatar emoji="🦅" name="Sage" color="cyan" />
    );
    expect(lastFrame()).toContain("Sage");
  });
});
