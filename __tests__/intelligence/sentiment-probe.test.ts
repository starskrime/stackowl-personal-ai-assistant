import { describe, it, expect } from "vitest";
import { SentimentProbe, classifySentiment } from "../../src/intelligence/sentiment-probe.js";

describe("classifySentiment", () => {
  it("classifies correction signals", () => {
    expect(classifySentiment("no, that's wrong")).toBe("correction");
    expect(classifySentiment("actually it should be")).toBe("correction");
    expect(classifySentiment("incorrect, try again")).toBe("correction");
  });

  it("classifies positive signals", () => {
    expect(classifySentiment("thanks, perfect!")).toBe("positive");
    expect(classifySentiment("that worked great")).toBe("positive");
    expect(classifySentiment("exactly what I needed")).toBe("positive");
  });

  it("classifies neutral signals", () => {
    expect(classifySentiment("ok")).toBe("neutral");
    expect(classifySentiment("what's next?")).toBe("neutral");
    expect(classifySentiment("")).toBe("neutral");
  });
});

describe("SentimentProbe", () => {
  it("increments challenge_instances on correction", () => {
    const updates: Array<{ sentiment: string; challengeIncrement: boolean }> = [];
    const probe = new SentimentProbe((s, c) => { updates.push({ sentiment: s, challengeIncrement: c }); });
    probe.onNextMessage("no that's not right");
    expect(updates[0]?.sentiment).toBe("correction");
    expect(updates[0]?.challengeIncrement).toBe(true);
  });

  it("does not increment challenge_instances on positive", () => {
    const updates: Array<{ sentiment: string; challengeIncrement: boolean }> = [];
    const probe = new SentimentProbe((s, c) => { updates.push({ sentiment: s, challengeIncrement: c }); });
    probe.onNextMessage("perfect, thanks!");
    expect(updates[0]?.sentiment).toBe("positive");
    expect(updates[0]?.challengeIncrement).toBe(false);
  });
});
