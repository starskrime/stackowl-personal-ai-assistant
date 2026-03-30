---
name: git_commit
description: Stage changed files and create a git commit with a descriptive conventional commit message
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📦"
parameters:
  files:
    type: string
    description: "Files to stage (use 'all' for all changes)"
    default: "all"
  commit_type:
    type: string
    description: "Conventional commit type (feat, fix, docs, style, refactor, test, chore)"
    default: "feat"
  scope:
    type: string
    description: "Scope of the change (e.g., auth, ui, api)"
    default: ""
  message:
    type: string
    description: "Commit description"
required: [message]
steps:
  - id: git_status
    tool: ShellTool
    args:
      command: "git status --short"
      mode: "local"
    timeout_ms: 5000
  - id: git_diff
    tool: ShellTool
    args:
      command: "git diff --stat"
      mode: "local"
    timeout_ms: 5000
  - id: git_add
    tool: ShellTool
    args:
      command: "git add {{#if files}}'{{files}}'{{else}}.{{/if}}"
      mode: "local"
    timeout_ms: 5000
  - id: git_commit
    tool: ShellTool
    args:
      command: "git commit -m '{{commit_type}}{{#if scope}}({{scope}}){{/if}}: {{message}}'"
      mode: "local"
    timeout_ms: 5000
  - id: git_confirm
    tool: ShellTool
    args:
      command: "git log --oneline -1"
      mode: "local"
    timeout_ms: 5000
  - id: present_result
    type: llm
    prompt: "Confirm the git commit was created successfully.\n\nStatus:\n{{git_status.output}}\n\nChanges:\n{{git_diff.output}}\n\nCommitted:\n{{git_commit.output}}\n\nLatest commit:\n{{git_confirm.output}}"
    depends_on: [git_status, git_diff, git_add, git_commit, git_confirm]
    inputs: [git_status.output, git_diff.output, git_commit.output, git_confirm.output]
---

# Git Commit

Stage changes and commit with a well-formatted message.

## Usage

```bash
/git_commit message="add OAuth2 login flow"
/git_commit files="src/auth" commit_type=feat scope=auth message="add OAuth2 login flow"
```

## Parameters

- **files**: Files to stage (use 'all' for all changes, default: all)
- **commit_type**: Conventional commit type (feat, fix, docs, style, refactor, test, chore, default: feat)
- **scope**: Scope of the change (e.g., auth, ui, api, default: empty)
- **message**: Commit description (required)

## Error Handling

- **Not a git repo:** Run `git init` or inform user.
- **Nothing to commit:** Show "Working tree clean" message.
- **Merge conflicts:** Show conflicted files and ask user to resolve.
