import { describe, it, expect } from "vitest";
import { PythonAnalyzer } from "../../src/evolution/python-analyzer.js";

describe("PythonAnalyzer.analyze", () => {
  it("flags subprocess import", () => {
    const result = PythonAnalyzer.analyze(`import subprocess\nsubprocess.run(["ls"])`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("subprocess");
  });

  it("flags os.system usage", () => {
    const result = PythonAnalyzer.analyze(`import os\nos.system("rm -rf /")`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("os.system");
  });

  it("flags eval()", () => {
    const result = PythonAnalyzer.analyze(`result = eval(user_input)`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("eval");
  });

  it("flags exec()", () => {
    const result = PythonAnalyzer.analyze(`exec(code)`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("exec");
  });

  it("flags __import__", () => {
    const result = PythonAnalyzer.analyze(`mod = __import__("os")`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("__import__");
  });

  it("passes safe data-processing code", () => {
    const code = `
import json, csv, sys

def execute(args: dict, cwd: str) -> str:
    with open(args["path"]) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return json.dumps(rows)

if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`;
    const result = PythonAnalyzer.analyze(code);
    expect(result.safe).toBe(true);
    expect(result.patterns).toHaveLength(0);
  });
});
