---
name: github_pr
description: Create GitHub pull requests with title, description, and labels using the GitHub CLI
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔀"
parameters:
  action:
    type: string
    description: "Action: create, list, or view"
    default: "create"
  title:
    type: string
    description: "PR title"
  body:
    type: string
    description: "PR description"
  base:
    type: string
    description: "Base branch to merge into"
    default: "main"
  number:
    type: number
    description: "PR number to view"
required: [action]
steps:
  - id: check_gh
    tool: ShellTool
    args:
      command: "which gh && gh auth status"
      mode: "local"
    timeout_ms: 15000
  - id: create_pr
    tool: ShellTool
    args:
      command: "gh pr create --title '{{title}}' --body '{{body}}' --base {{base}}"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: list_prs
    tool: ShellTool
    args:
      command: "gh pr list"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: view_pr
    tool: ShellTool
    args:
      command: "gh pr view {{number}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
---

# GitHub Pull Request

Create and manage GitHub PRs.

## Usage

```bash
/github_pr action=<create|list|view> title=<title> body=<body> base=<branch> number=<num>
```

## Parameters

- **action**: Action: create, list, or view (default: create)
- **title**: PR title
- **body**: PR description
- **base**: Base branch to merge into (default: main)
- **number**: PR number to view

## Examples

### Create a PR

```
action=create
title=feat: add auth module
body=Implements OAuth2 login
base=main
```

### List open PRs

```
action=list
```

## Error Handling

- **gh not installed:** `brew install gh`.
- **Not authenticated:** `gh auth login`.
- **No upstream:** Set with `git push -u origin <branch>`.
