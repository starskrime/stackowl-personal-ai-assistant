---
name: changelog_writer
description: Generate a changelog from git commit history following the Keep a Changelog format
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📓"
parameters:
  since:
    type: string
    description: "Show commits since this date (YYYY-MM-DD or natural language like '1 month ago')"
    default: "1 month ago"
  file:
    type: string
    description: "Output file path (default: CHANGELOG.md in current directory)"
    default: "CHANGELOG.md"
required: []
steps:
  - id: get_commits
    tool: ShellTool
    args:
      command: "git log --oneline --since='{{since}}' 2>/dev/null || git log --oneline -50"
      mode: "local"
    timeout_ms: 10000
  - id: categorize_commits
    type: llm
    prompt: "Categorize these git commits following the Keep a Changelog format. Group by: Added (feat), Fixed (fix), Documentation (docs), Changed (refactor), Breaking Changes (BREAKING CHANGE). Output as markdown with today's date.\n\nCommits:\n{{get_commits.stdout}}"
    depends_on: [get_commits]
    inputs: [get_commits.stdout]
  - id: write_changelog
    tool: WriteFileTool
    args:
      path: "{{file}}"
      content: "{{categorize_commits.output}}"
    optional: true
---

# Changelog Writer

Generate a changelog from git history.

## Steps

1. **Get recent commits:**
   ```bash
   git log --oneline --since='1 month ago'
   ```
2. **Categorize by conventional commit type:**
   - `feat` → Added
   - `fix` → Fixed
   - `docs` → Documentation
   - `refactor` → Changed
   - `BREAKING CHANGE` → Breaking Changes
3. **Format as Keep a Changelog:**
   ```markdown
   ## [Unreleased] - YYYY-MM-DD

   ### Added

   - New feature X

   ### Fixed

   - Bug in Y
   ```
4. **Save** to `CHANGELOG.md`.

## Examples

### Generate from last month

```bash
git log --oneline --since='2026-02-22'
```

## Error Handling

- **No conventional commits:** Parse commit messages best-effort.
- **Not a git repo:** Inform user.
