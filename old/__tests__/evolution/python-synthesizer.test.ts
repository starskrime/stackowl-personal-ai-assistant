import { describe, it, expect, vi, beforeEach } from "vitest";
import { PythonSynthesizer } from "../../src/evolution/python-synthesizer.js";
import type { CapabilityGap } from "../../src/evolution/detector.js";

const mockProvider = {
  chat: vi.fn().mockResolvedValue({
    content: `# TOOL_NAME: synth_csv_parser
# DESCRIPTION: Parse a CSV file and return rows as JSON
# PARAMETERS:
#   file_path: string - path to the CSV file
import json, csv, sys

def execute(args: dict, cwd: str) -> str:
    with open(args["file_path"]) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return json.dumps(rows)

if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`,
    usage: { promptTokens: 100, completionTokens: 200 },
  }),
};

beforeEach(() => {
  mockProvider.chat.mockClear();
});

describe("PythonSynthesizer.generate", () => {
  it("returns generated Python code starting with TOOL_NAME header", async () => {
    const gap: CapabilityGap = {
      type: "CAPABILITY_GAP",
      userRequest: "parse this CSV file",
      description: "Need to parse CSV data",
    };
    const synth = new PythonSynthesizer();
    const result = await synth.generate(gap, mockProvider as any, "claude-sonnet-4-6");
    expect(result.code).toContain("TOOL_NAME");
    expect(result.code).toContain("def execute");
    expect(result.code).toContain("if __name__");
    expect(result.toolName).toMatch(/^synth_/u);
  });

  it("uses the provider.chat method exactly once", async () => {
    const gap: CapabilityGap = { type: "CAPABILITY_GAP", userRequest: "x", description: "x" };
    const synth = new PythonSynthesizer();
    await synth.generate(gap, mockProvider as any, "model");
    expect(mockProvider.chat).toHaveBeenCalledOnce();
  });
});
