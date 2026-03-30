---
name: refactor_code
description: Refactor code to improve readability, reduce duplication, and follow clean code principles
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "♻️"
parameters:
  file_path:
    type: string
    description: "Path to the file to refactor"
required: [file_path]
steps:
  - id: read_file
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
  - id: analyze_code
    type: llm
    prompt: "Analyze the code for refactoring opportunities:\n- Duplicate code → Extract functions\n- Long functions → Split into smaller ones\n- Magic numbers → Extract constants\n- Deep nesting → Early returns / guard clauses\n- Dead code → Remove unused variables/functions\n- Poor naming → Rename for clarity\n\nCode:\n{{read_file.output}}\n\nProvide a detailed refactoring plan."
    depends_on: [read_file]
    inputs: [read_file.output]
  - id: apply_refactoring
    tool: WriteFileTool
    args:
      path: "{{file_path}}"
      content: "{{analyze_code.output.refactored_code || analyze_code.output}}"
    optional: true
    depends_on: [analyze_code]
  - id: run_tests
    tool: ShellTool
    args:
      command: "npm test 2>&1 || python -m pytest 2>&1 || echo 'No tests found'"
      mode: "local"
    optional: true
    depends_on: [apply_refactoring]
---

# Refactor Code

Improve code quality through systematic refactoring.

## Usage

```bash
/refactor_code file_path=./src/utils/helpers.ts
```

## Parameters

- **file_path**: Path to the file to refactor (required)

## Examples

### Refactor a file

```bash
read_file("src/utils/helpers.ts")
```

## Error Handling

- **Tests fail after refactoring:** Revert changes and try smaller refactoring steps.
- **File too large:** Focus on one function/section at a time.
