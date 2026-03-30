---
name: dependency_check
description: Audit project dependencies for outdated packages, known vulnerabilities, and unused imports
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔎"
parameters:
  project_path:
    type: string
    description: "Path to the project (default: current directory)"
    default: "."
required: []
steps:
  - id: detect_package_manager
    tool: ShellTool
    args:
      command: "ls {{project_path}}/package.json {{project_path}}/requirements.txt {{project_path}}/go.mod {{project_path}}/Cargo.toml 2>/dev/null | xargs -I{} basename {}"
      mode: "local"
    timeout_ms: 5000
  - id: check_npm_outdated
    tool: ShellTool
    args:
      command: "cd {{project_path}} && npm outdated 2>/dev/null || echo 'Not an npm project'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: check_npm_audit
    tool: ShellTool
    args:
      command: "cd {{project_path}} && npm audit 2>/dev/null || echo 'npm audit not available'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: check_pip_outdated
    tool: ShellTool
    args:
      command: "cd {{project_path}} && pip list --outdated 2>/dev/null || echo 'Not a pip project'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: check_go_outdated
    tool: ShellTool
    args:
      command: "cd {{project_path}} && go list -m -u all 2>/dev/null || echo 'Not a Go project'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: generate_report
    type: llm
    prompt: "Create a dependency audit report for the project at '{{project_path}}'.\n\nDetected package managers: {{detect_package_manager.stdout}}\n\nnpm outdated:\n{{check_npm_outdated.stdout}}\n\nnpm audit:\n{{check_npm_audit.stdout}}\n\npip outdated:\n{{check_pip_outdated.stdout}}\n\ngo list -m -u:\n{{check_go_outdated.stdout}}\n\nFormat as markdown with sections for:\n1. Vulnerabilities (severity, package, description)\n2. Outdated packages (current → latest)\n3. Recommendations (e.g., 'Run npm audit fix')"
    depends_on: [detect_package_manager, check_npm_outdated, check_npm_audit, check_pip_outdated, check_go_outdated]
    inputs: [detect_package_manager.stdout, check_npm_outdated.stdout, check_npm_audit.stdout, check_pip_outdated.stdout, check_go_outdated.stdout]
---

# Dependency Check

Audit project dependencies for updates, vulnerabilities, and unused packages.

## Steps

1. **Detect package manager:**

   ```bash
   ls package.json requirements.txt go.mod Cargo.toml 2>/dev/null
   ```

2. **Check for outdated packages:**
   - **npm:** `npm outdated`
   - **pip:** `pip list --outdated`
   - **go:** `go list -m -u all`

3. **Check for vulnerabilities:**
   - **npm:** `npm audit`
   - **pip:** `pip-audit 2>/dev/null || echo 'pip-audit not installed'`

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

```
project_path="./my-project"
```

## Error Handling

- **No package manager found:** Ask user which package manager they use.
- **npm audit fails:** Try `npm audit --json` for parseable output.
- **Network error:** Note that vulnerability check requires internet.
