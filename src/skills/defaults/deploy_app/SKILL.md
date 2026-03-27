---
name: deploy_app
description: Deploy applications by running build commands, pushing to git remotes, or executing deployment scripts
openclaw:
  emoji: "🚢"
---

# Deploy Application

Run deployment workflows.

## Steps

1. **Run tests first:**
   ```bash
   run_shell_command("npm test")
   ```
2. **Build:**
   ```bash
   run_shell_command("npm run build")
   ```
3. **Push to remote:**
   ```bash
   run_shell_command("git push origin main")
   ```
4. **Or run deploy script:**
   ```bash
   run_shell_command("./deploy.sh")
   ```
5. **Verify deployment:**
   ```bash
   run_shell_command("curl -s -o /dev/null -w '%{http_code}' <production_url>")
   ```

## Examples

### Deploy Node.js app

```bash
run_shell_command("npm run build && git push origin main")
```

## Error Handling

- **Tests fail:** Abort deployment and show failures.
- **Build fails:** Show error and suggest fixes.
- **Push rejected:** Pull first with `git pull --rebase`.
