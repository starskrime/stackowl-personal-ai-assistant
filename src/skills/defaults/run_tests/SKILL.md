---
name: run_tests
description: Detect the project's test framework and execute the test suite, reporting results clearly
openclaw:
  emoji: "🧪"
---

# Run Tests

Auto-detect and execute the project's test suite.

## Steps

1. **Detect the test framework** by checking config files:

   ```bash
   run_shell_command("ls package.json pyproject.toml Cargo.toml Makefile go.mod 2>/dev/null")
   ```

2. **Run the appropriate test command:**
   - **Node.js (vitest/jest):** `run_shell_command("npm test")`
   - **Python (pytest):** `run_shell_command("python -m pytest -v")`
   - **Go:** `run_shell_command("go test ./...")`
   - **Rust:** `run_shell_command("cargo test")`
   - **Makefile:** `run_shell_command("make test")`

3. **Parse and summarize results:**
   - Total tests / passed / failed / skipped
   - List of failing test names with error messages
   - Execution time

4. **Present a clear summary** to the user.

## Examples

### Run Node.js tests

```bash
run_shell_command("npm test 2>&1")
```

### Run Python tests

```bash
run_shell_command("python -m pytest -v --tb=short 2>&1")
```

## Error Handling

- **No test framework detected:** Ask user which framework they use.
- **Tests fail:** Show failures clearly and offer to help debug.
- **Missing dependencies:** Suggest `npm install` or `pip install -r requirements.txt`.
