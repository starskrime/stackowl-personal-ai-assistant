import { describe, it, expect, vi, beforeEach } from "vitest";
import * as childProcessModule from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";

vi.mock("node:child_process", () => ({
  execFile: vi.fn(),
}));

import { PythonAdapter } from "../../src/evolution/python-adapter.js";
import type { ToolContext } from "../../src/tools/registry.js";

const mockContext: ToolContext = { cwd: "/workspace" };

beforeEach(() => {
  vi.resetAllMocks();
});

describe("PythonAdapter.wrap", () => {
  it("returns a ToolImplementation with correct definition from header", async () => {
    const code = `
# TOOL_NAME: synth_csv_parser
# DESCRIPTION: Parse a CSV file and return rows as JSON
# PARAMETERS:
#   file_path: string - path to the CSV file
import json, csv, sys
def execute(args, cwd): pass
if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`;
    const tool = PythonAdapter.wrap(join(tmpdir(), "csv_parser.py"), code);
    expect(tool.definition.name).toBe("synth_csv_parser");
    expect(tool.definition.description).toBe("Parse a CSV file and return rows as JSON");
    expect(tool.source).toBe("synthesized");
  });

  it("execute() calls python3 with correct args and returns stdout", async () => {
    const { execFile } = childProcessModule;
    vi.mocked(execFile).mockImplementation((_cmd, _args, _opts, cb: any) => {
      cb(null, '["row1","row2"]', "");
      return {} as any;
    });

    const tool = PythonAdapter.wrap("/workspace/synthesized/tools/csv_parser.py", `# TOOL_NAME: synth_csv_parser\n# DESCRIPTION: desc`);
    const result = await tool.execute({ file_path: "data.csv" }, mockContext);
    expect(result).toBe('["row1","row2"]');
    expect(vi.mocked(execFile)).toHaveBeenCalledWith(
      "python3",
      ["/workspace/synthesized/tools/csv_parser.py", expect.any(String), "/workspace"],
      expect.objectContaining({ timeout: 30000, cwd: "/workspace" }),
      expect.any(Function),
    );
  });

  it("execute() returns stderr message on process error", async () => {
    const { execFile } = childProcessModule;
    vi.mocked(execFile).mockImplementation((_cmd, _args, _opts, cb: any) => {
      cb(new Error("python3 not found"), "", "python3: not found");
      return {} as any;
    });

    const tool = PythonAdapter.wrap(join(tmpdir(), "tool.py"), `# TOOL_NAME: synth_x\n# DESCRIPTION: x`);
    const result = await tool.execute({}, mockContext);
    expect(result).toContain("ERROR");
    expect(result).toContain("python3 not found");
  });
});
