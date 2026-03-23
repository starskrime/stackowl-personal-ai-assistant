---
name: process_manager
description: List running processes, find resource-heavy tasks, and kill unresponsive applications on macOS
openclaw:
  emoji: "⚙️"
  os: [darwin]
---
# Process Manager
Monitor and manage running processes.
## Steps
1. **List top processes by CPU/memory:**
   ```bash
   run_shell_command("ps aux --sort=-%cpu | head -15")
   run_shell_command("ps aux --sort=-%mem | head -15")
   ```
2. **Find a specific process:**
   ```bash
   run_shell_command("pgrep -fl '<process_name>'")
   ```
3. **Kill a process (with user confirmation):**
   ```bash
   run_shell_command("kill <PID>")
   ```
   Force kill if unresponsive:
   ```bash
   run_shell_command("kill -9 <PID>")
   ```
## Examples
### Find memory hogs
```bash
run_shell_command("ps aux --sort=-%mem | head -10")
```
### Kill unresponsive app
```bash
run_shell_command("pkill -f 'AppName'")
```
## Error Handling
- **Process not found:** Verify the name and try partial match with `pgrep -il`.
- **Permission denied:** Some system processes require `sudo`—inform user.
- **Critical process:** Warn before killing system processes (kernel_task, WindowServer, etc.).
