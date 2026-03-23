---
name: system_update
description: Check for available macOS system updates and Homebrew package updates
openclaw:
  emoji: "🔄"
  os: [darwin]
---
# System Update Check
Check for macOS and Homebrew updates.
## Steps
1. **Check macOS updates:**
   ```bash
   run_shell_command("softwareupdate --list 2>&1")
   ```
2. **Check Homebrew updates:**
   ```bash
   run_shell_command("brew update && brew outdated")
   ```
3. **Present summary** of available updates.
## Examples
### Full update check
```bash
run_shell_command("softwareupdate --list && brew outdated")
```
## Error Handling
- **Homebrew not installed:** Skip brew section and note it.
- **Requires restart:** Warn user about updates that need restart.
