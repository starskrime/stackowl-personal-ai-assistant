---
name: git_branch
description: Create, switch, list, or delete git branches with naming convention enforcement
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🌿"
parameters:
  action:
    type: string
    description: "Action: list, create, switch, or delete"
  branch_name:
    type: string
    description: "Name of the branch"
required: [action]
steps:
  - id: list_branches
    tool: ShellTool
    args:
      command: "git branch -a"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: create_branch
    tool: ShellTool
    args:
      command: "git checkout -b {{branch_name}}"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: switch_branch
    tool: ShellTool
    args:
      command: "git checkout {{branch_name}}"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: delete_branch
    tool: ShellTool
    args:
      command: "git branch -d {{branch_name}}"
      mode: "local"
    timeout_ms: 10000
    optional: true
---

# Git Branch Management

Create, switch, list, and delete git branches.

## Usage

```bash
/git_branch action=<list|create|switch|delete> branch_name=<name>
```

## Parameters

- **action**: Action: list, create, switch, or delete
- **branch_name**: Name of the branch

## Examples

### Create feature branch

```
action=create
branch_name=feature/add-notifications
```

### List all branches

```
action=list
```

### Switch to a branch

```
action=switch
branch_name=develop
```

## Error Handling

- **Branch already exists:** Suggest switching to it or using a different name.
- **Uncommitted changes:** Warn user and suggest stashing: `git stash`.
- **Delete protected branch (main/master):** Refuse and explain why.
