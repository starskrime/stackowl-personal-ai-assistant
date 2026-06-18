import { describe, it, expect } from "vitest";
import { UserFacingStatusNarrator } from "../src/engine/user-facing-narrator.js";

const narrator = new UserFacingStatusNarrator();

describe("UserFacingStatusNarrator", () => {
  it("strips EXHAUSTION_MARKER", () => { expect(narrator.postProcess("I tried. __STACKOWL_EXHAUSTED__", 0.3)).not.toContain("__STACKOWL_EXHAUSTED__"); });
  it("strips CAPABILITY_GAP markers", () => { expect(narrator.postProcess("Can't. [CAPABILITY_GAP: need X]", 0.5)).not.toContain("[CAPABILITY_GAP"); });
  it("strips SYSTEM markers", () => { expect(narrator.postProcess("[SYSTEM: replan] Working.", 0.7)).not.toContain("[SYSTEM:"); });
  it("translates HTTP error jargon", () => { expect(narrator.postProcess("Got HTTP 429.", 0.6)).not.toContain("HTTP 429"); });
  it("builds tier-1 degradation", () => { expect(narrator.buildDegradation(1, "Here is the answer.", undefined, undefined)).toContain("Here is the answer"); });
  it("builds tier-3 degradation without 'undefined' text", () => { expect(narrator.buildDegradation(3, "", "need login", undefined)).not.toContain("undefined"); });
  it("statusMessage returns non-empty string", () => { expect(narrator.statusMessage("tool_executing").length).toBeGreaterThan(0); });
});
