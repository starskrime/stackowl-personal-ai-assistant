---
name: lint_fix
description: Run code linters and automatically fix formatting and style issues in the project
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✨"
parameters:
  project_type:
    type: string
    description: "Project type: javascript, typescript, python, or auto-detect"
    default: "auto"
  path:
    type: string
    description: "Path to lint (default: current directory)"
    default: "."
required: []
steps:
  - id: detect_project
    tool: ShellTool
    args:
      command: "ls .eslintrc* .prettierrc* pyproject.toml .flake8 2>/dev/null"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: run_eslint
    tool: ShellTool
    args:
      command: "npx eslint --fix {{path}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: run_prettier
    tool: ShellTool
    args:
      command: "npx prettier --write {{path}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: run_black
    tool: ShellTool
    args:
      command: "python -m black {{path}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: run_ruff
    tool: ShellTool
    args:
      command: "ruff check --fix {{path}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
---

# Lint and Fix

Run linters and auto-fix code style issues.

## Usage

```bash
/lint_fix project_type=<type> path=<path>
```

## Parameters

- **project_type**: Project type: javascript, typescript, python, or auto-detect (default: auto)
- **path**: Path to lint (default: current directory)

## Examples

### Fix JavaScript project

```
project_type=javascript
path=src/
```

### Fix Python project

```
project_type=python
path=.
```

## Error Handling

- **Linter not installed:** Suggest installation command.
- **Config file missing:** Use default config or create a basic one.
