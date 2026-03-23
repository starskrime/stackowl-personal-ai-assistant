---
name: changelog_writer
description: Generate a changelog from git commit history following the Keep a Changelog format
openclaw:
  emoji: "📓"
---
# Changelog Writer
Generate a changelog from git history.
## Steps
1. **Get recent commits:**
   ```bash
   run_shell_command("git log --oneline --since='1 month ago'")
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
run_shell_command("git log --oneline --since='2026-02-22'")
```
## Error Handling
- **No conventional commits:** Parse commit messages best-effort.
- **Not a git repo:** Inform user.
