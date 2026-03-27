---
name: lint_fix
description: Run code linters and automatically fix formatting and style issues in the project
openclaw:
  emoji: "✨"
---

# Lint and Fix

Run linters and auto-fix code style issues.

## Steps

1. **Detect the project type and linter:**
   ```bash
   run_shell_command("ls .eslintrc* .prettierrc* pyproject.toml .flake8 2>/dev/null")
   ```
2. **Run the linter with auto-fix:**
   - **ESLint:** `run_shell_command("npx eslint --fix src/")`
   - **Prettier:** `run_shell_command("npx prettier --write src/")`
   - **Python (black):** `run_shell_command("python -m black .")`
   - **Python (ruff):** `run_shell_command("ruff check --fix .")`
3. **Show summary** of fixes applied.

## Examples

### Fix JavaScript project

```bash
run_shell_command("npx eslint --fix src/ && npx prettier --write src/")
```

## Error Handling

- **Linter not installed:** Suggest installation command.
- **Config file missing:** Use default config or create a basic one.
