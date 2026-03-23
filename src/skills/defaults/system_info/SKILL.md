---
name: system_info
description: Display comprehensive macOS system information including CPU, RAM, disk, OS version, and uptime
openclaw:
  emoji: "💻"
  os: [darwin]
---
# System Info
Get detailed system status on macOS.
## Steps
1. **Gather system information:**
   ```bash
   run_shell_command("system_profiler SPHardwareDataType 2>/dev/null | grep -E 'Model|Chip|Memory|Serial'")
   run_shell_command("sw_vers")
   run_shell_command("uptime")
   run_shell_command("df -h / | tail -1")
   run_shell_command("top -l 1 | head -10")
   ```
2. **Format as a clean summary:**
   - macOS version and build
   - CPU/chip model
   - RAM total and used
   - Disk usage (used/total)
   - Uptime
## Examples
### Quick system overview
```bash
run_shell_command("sw_vers && uptime && df -h /")
```
## Error Handling
- **system_profiler slow:** Use faster alternatives like `sysctl`.
- **Permission denied:** Some info requires admin access; skip and note.
