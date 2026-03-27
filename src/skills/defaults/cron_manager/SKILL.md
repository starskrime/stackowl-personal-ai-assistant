---
name: cron_manager
description: List, create, edit, and delete cron jobs for scheduled task automation
openclaw:
  emoji: "⏲️"
---

# Cron Job Manager

Manage scheduled cron tasks.

## Steps

1. **List current cron jobs:**
   ```bash
   run_shell_command("crontab -l 2>/dev/null || echo 'No crontab'")
   ```
2. **Add a cron job:**
   ```bash
   run_shell_command("(crontab -l 2>/dev/null; echo '<schedule> <command>') | crontab -")
   ```
   Schedule format: `minute hour day month weekday`
3. **Remove a cron job:**
   ```bash
   run_shell_command("crontab -l | grep -v '<pattern>' | crontab -")
   ```

## Examples

### Run script daily at 9am

```bash
run_shell_command("(crontab -l 2>/dev/null; echo '0 9 * * * /path/to/script.sh') | crontab -")
```

## Error Handling

- **Invalid schedule:** Validate cron syntax before adding.
- **Script not executable:** `chmod +x <script>`.
