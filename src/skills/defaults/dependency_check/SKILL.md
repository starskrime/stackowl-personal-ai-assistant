---
name: dependency_check
description: Audit project dependencies for outdated packages, known vulnerabilities, and unused imports
openclaw:
  emoji: "🔎"
---

# Dependency Check

Audit project dependencies for updates, vulnerabilities, and unused packages.

## Steps

1. **Detect package manager:**
   ```bash
   run_shell_command("ls package.json requirements.txt go.mod Cargo.toml 2>/dev/null")
   ```

2. **Check for outdated packages:**
   - **npm:** `run_shell_command("npm outdated")`
   - **pip:** `run_shell_command("pip list --outdated")`
   - **go:** `run_shell_command("go list -m -u all")`

3. **Check for vulnerabilities:**
   - **npm:** `run_shell_command("npm audit")`
   - **pip:** `run_shell_command("pip-audit 2>/dev/null || echo 'pip-audit not installed'")`

4. **Present a summary:**
   ```markdown
   ## Dependency Audit

   ### ⚠️ Vulnerabilities
   - <package>: <severity> — <description>

   ### 📦 Outdated
   - <package>: <current> → <latest>

   ### Recommendations
   - Run `npm audit fix` to auto-fix N issues
   ```

## Examples

### Audit npm project
```bash
run_shell_command("npm outdated && npm audit")
```

## Error Handling

- **No package manager found:** Ask user which package manager they use.
- **npm audit fails:** Try `npm audit --json` for parseable output.
- **Network error:** Note that vulnerability check requires internet.
