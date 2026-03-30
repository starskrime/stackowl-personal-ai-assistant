---
name: code_review
description: Review code files or git diffs for bugs, security issues, performance problems, and style violations
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔍"
parameters:
  source:
    type: string
    description: "Source to review (file path, 'staged', 'last', or commit hash)"
    default: "staged"
  file_path:
    type: string
    description: "File path to review (if source=file)"
required: []
steps:
  - id: get_staged_diff
    tool: ShellTool
    args:
      command: "git diff --cached"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: get_last_commit_diff
    tool: ShellTool
    args:
      command: "git diff HEAD~1"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: get_file_content
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
    optional: true
  - id: review_code
    type: llm
    prompt: "Review the code for quality, bugs, security, and best practices.\n\nAnalyze across dimensions:\n- **Bugs:** Logic errors, off-by-one, null/undefined\n- **Security:** SQL injection, XSS, hardcoded secrets\n- **Performance:** N+1 queries, unnecessary loops, memory leaks\n- **Style:** Naming conventions, code organization, DRY violations\n- **Error handling:** Missing try/catch, swallowed exceptions\n\nCode to review:\n{{#if get_file_content.output}}File: {{file_path}}\n{{get_file_content.output}}{{/if}}\n{{#if get_staged_diff.output}}Staged changes:\n{{get_staged_diff.output}}{{/if}}\n{{#if get_last_commit_diff.output}}Last commit diff:\n{{get_last_commit_diff.output}}{{/if}}\n\nPresent findings as a structured review with sections for Bugs, Security, Performance, Style, and What's Good."
    depends_on: [get_staged_diff, get_last_commit_diff, get_file_content]
    inputs: [get_staged_diff.output, get_last_commit_diff.output, get_file_content.output]
---

# Code Review

Analyze code for quality, bugs, security, and best practices.

## Usage

```bash
/code_review
/code_review source=file file_path="src/auth/login.ts"
```

## Parameters

- **source**: Source to review (file path, 'staged', 'last', or commit hash, default: staged)
- **file_path**: File path to review (if source=file)

## Examples

```
code_review source=staged
code_review source=last
code_review source=file file_path="src/auth/login.ts"
```

## Error Handling

- **File not found:** Ask user for correct path, use `find` to search.
- **Binary file:** Skip and note "Cannot review binary files."
- **Very large diff (>500 lines):** Focus on the most critical files first.
