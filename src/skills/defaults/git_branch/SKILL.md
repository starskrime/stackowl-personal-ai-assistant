---
name: git_branch
description: Create, switch, list, or delete git branches with naming convention enforcement
openclaw:
  emoji: "🌿"
---

# Git Branch Management

Create, switch, list, and delete git branches.

## Steps

1. **List existing branches:**

   ```bash
   run_shell_command("git branch -a")
   ```

2. **Create a new branch:**

   ```bash
   run_shell_command("git checkout -b <branch_name>")
   ```

   Enforce naming: `<type>/<description>` (e.g., `feature/add-auth`, `fix/login-bug`)

3. **Switch branches:**

   ```bash
   run_shell_command("git checkout <branch_name>")
   ```

4. **Delete a branch:**
   ```bash
   run_shell_command("git branch -d <branch_name>")
   ```

## Examples

### Create feature branch

```bash
run_shell_command("git checkout -b feature/add-notifications")
```

### List all branches

```bash
run_shell_command("git branch -a --sort=-committerdate")
```

## Error Handling

- **Branch already exists:** Suggest switching to it or using a different name.
- **Uncommitted changes:** Warn user and suggest stashing: `git stash`.
- **Delete protected branch (main/master):** Refuse and explain why.
