import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

const msg: HeartbeatMessage = {
  id: "hb-1",
  owlId: "owl-sage",
  owlName: "Sage",
  owlEmoji: "🦅",
  text: "Deploy window closes at 5pm.",
  read: false,
  timestamp: Date.now(),
};

describe("HeartbeatBanner", () => {
  it("renders owl name and message text", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("Sage");
    expect(lastFrame()).toContain("Deploy window closes at 5pm.");
  });

  it("renders the unsolicited label", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("unsolicited");
  });

  it("renders the owl emoji", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("🦅");
  });
});
