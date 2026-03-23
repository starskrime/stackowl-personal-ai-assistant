---
name: disk_cleanup
description: Find and remove large unused files, caches, and temporary data to free up disk space on macOS
openclaw:
  emoji: "🧹"
  os: [darwin]
---
# Disk Cleanup
Free up disk space by cleaning caches and finding large files.
## Steps
1. **Show current disk usage:**
   ```bash
   run_shell_command("df -h /")
   ```
2. **Find large files (>100MB):**
   ```bash
   run_shell_command("find ~ -type f -size +100M 2>/dev/null | head -20")
   ```
3. **Calculate cache sizes:**
   ```bash
   run_shell_command("du -sh ~/Library/Caches/ 2>/dev/null")
   run_shell_command("du -sh /tmp/ 2>/dev/null")
   run_shell_command("du -sh ~/Library/Developer/Xcode/DerivedData 2>/dev/null")
   ```
4. **Present cleanup options** to user. Only delete with explicit confirmation:
   ```bash
   run_shell_command("rm -rf ~/Library/Caches/<specific_app>/")
   ```
## Examples
### Show space usage
```bash
run_shell_command("du -sh ~/Library/Caches ~/Downloads ~/.Trash ~/Library/Developer 2>/dev/null")
```
## Error Handling
- **Permission denied:** Skip protected directories and note them.
- **Critical directory:** NEVER delete system files, ~/Documents, ~/Desktop without explicit confirmation.
