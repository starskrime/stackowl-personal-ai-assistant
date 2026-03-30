---
name: run_tests
description: Detect the project's test framework and execute the test suite, reporting results clearly
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧪"
parameters:
  test_path:
    type: string
    description: "Path to run tests in (default: current directory)"
    default: "."
  framework:
    type: string
    description: "Force specific test framework (vitest, jest, pytest, go, cargo, make)"
    default: ""
required: []
steps:
  - id: detect_framework
    tool: ShellTool
    args:
      command: "ls {{test_path}}/package.json {{test_path}}/pyproject.toml {{test_path}}/Cargo.toml {{test_path}}/Makefile {{test_path}}/go.mod 2>/dev/null | xargs -I{} basename {}"
      mode: "local"
  - id: run_npm_tests
    tool: ShellTool
    args:
      command: "cd {{test_path}} && npm test 2>&1"
      mode: "local"
    optional: true
    depends_on: [detect_framework]
  - id: run_pytest
    tool: ShellTool
    args:
      command: "cd {{test_path}} && python -m pytest -v --tb=short 2>&1"
      mode: "local"
    optional: true
    depends_on: [detect_framework]
  - id: run_go_tests
    tool: ShellTool
    args:
      command: "cd {{test_path}} && go test ./... 2>&1"
      mode: "local"
    optional: true
    depends_on: [detect_framework]
  - id: run_cargo_tests
    tool: ShellTool
    args:
      command: "cd {{test_path}} && cargo test 2>&1"
      mode: "local"
    optional: true
    depends_on: [detect_framework]
  - id: run_make_tests
    tool: ShellTool
    args:
      command: "cd {{test_path}} && make test 2>&1"
      mode: "local"
    optional: true
    depends_on: [detect_framework]
  - id: summarize_results
    type: llm
    prompt: "Summarize the test results clearly:\n- Total tests / passed / failed / skipped\n- List of failing test names with error messages\n- Execution time\n\nTest output:\n{{run_npm_tests.output || run_pytest.output || run_go_tests.output || run_cargo_tests.output || run_make_tests.output}}"
    depends_on: [run_npm_tests, run_pytest, run_go_tests, run_cargo_tests, run_make_tests]
    inputs: [run_npm_tests.output, run_pytest.output, run_go_tests.output, run_cargo_tests.output, run_make_tests.output]
---

# Run Tests

Auto-detect and execute the project's test suite.

## Usage

```bash
/run_tests
/run_tests test_path=./backend framework=pytest
```

## Parameters

- **test_path**: Path to run tests in (default: current directory)
- **framework**: Force specific test framework (vitest, jest, pytest, go, cargo, make)

## Examples

### Run Node.js tests

```bash
npm test 2>&1
```

### Run Python tests

```bash
python -m pytest -v --tb=short 2>&1
```

## Error Handling

- **No test framework detected:** Ask user which framework they use.
- **Tests fail:** Show failures clearly and offer to help debug.
- **Missing dependencies:** Suggest `npm install` or `pip install -r requirements.txt`.
