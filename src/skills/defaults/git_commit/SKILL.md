---
name: git_commit
description: Stage changed files and create a git commit with a descriptive conventional commit message
openclaw:
  emoji: "📦"
---

# Git Commit

Stage changes and commit with a well-formatted message.

## Steps

1. **Check current git status:**
   ```bash
   run_shell_command("git status --short")
   ```

2. **Review the diff:**
   ```bash
   run_shell_command("git diff --stat")
   ```

3. **Stage files:**
   ```bash
   run_shell_command("git add <files>")
   ```
   Or stage all: `run_shell_command("git add -A")`

4. **Generate a conventional commit message:**
   Format: `<type>(<scope>): <description>`
   Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

5. **Create the commit:**
   ```bash
   run_shell_command("git commit -m '<type>(<scope>): <description>'")
   ```

6. **Show commit confirmation:**
   ```bash
   run_shell_command("git log --oneline -1")
   ```

## Examples

### Commit a feature
```bash
run_shell_command("git add -A && git commit -m 'feat(auth): add OAuth2 login flow'")
```

## Error Handling

- **Not a git repo:** Run `git init` or inform user.
- **Nothing to commit:** Show "Working tree clean" message.
- **Merge conflicts:** Show conflicted files and ask user to resolve.
