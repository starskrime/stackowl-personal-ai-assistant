---
name: backup_files
description: Create timestamped backups of important files or directories as compressed archives
openclaw:
  emoji: "💾"
---

# Backup Files

Create compressed backups of files or directories.

## Steps

1. **Determine what to backup** from user request.
2. **Create timestamped backup:**
   ```bash
   run_shell_command("tar -czf ~/Backups/backup_$(date +%Y%m%d_%H%M%S).tar.gz <source_path>")
   ```
3. **Verify the backup:**
   ```bash
   run_shell_command("ls -lh ~/Backups/backup_*.tar.gz | tail -1")
   ```
4. **Confirm** with file size and location.

## Examples

### Backup Documents folder

```bash
run_shell_command("mkdir -p ~/Backups && tar -czf ~/Backups/docs_$(date +%Y%m%d).tar.gz ~/Documents/")
```

## Error Handling

- **Backup directory doesn't exist:** Create it with `mkdir -p ~/Backups`.
- **Not enough disk space:** Check with `df -h` before starting.
- **Permission denied:** Skip protected files and note them in the backup log.
