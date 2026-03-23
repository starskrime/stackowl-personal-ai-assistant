---
name: github_pr
description: Create GitHub pull requests with title, description, and labels using the GitHub CLI
openclaw:
  emoji: "🔀"
---
# GitHub Pull Request
Create and manage GitHub PRs.
## Steps
1. **Check gh CLI is available:**
   ```bash
   run_shell_command("which gh && gh auth status")
   ```
2. **Create PR:**
   ```bash
   run_shell_command("gh pr create --title '<title>' --body '<description>' --base main")
   ```
3. **List open PRs:**
   ```bash
   run_shell_command("gh pr list")
   ```
4. **View PR details:**
   ```bash
   run_shell_command("gh pr view <number>")
   ```
## Examples
### Create a PR
```bash
run_shell_command("gh pr create --title 'feat: add auth module' --body 'Implements OAuth2 login' --base main")
```
## Error Handling
- **gh not installed:** `brew install gh`.
- **Not authenticated:** `gh auth login`.
- **No upstream:** Set with `git push -u origin <branch>`.
