---
name: app_launcher
description: Launch, quit, or check if a macOS application is running
openclaw:
  emoji: "🚀"
  os: [darwin]
---
# App Launcher
Launch and manage macOS applications.
## Steps
1. **Launch an app:**
   ```bash
   run_shell_command("open -a '<AppName>'")
   ```
2. **Quit an app:**
   ```bash
   run_shell_command("osascript -e 'tell application \"<AppName>\" to quit'")
   ```
3. **Check if running:**
   ```bash
   run_shell_command("pgrep -x '<AppName>' && echo 'Running' || echo 'Not running'")
   ```
## Examples
### Launch Safari
```bash
run_shell_command("open -a 'Safari'")
```
### Quit Slack
```bash
run_shell_command("osascript -e 'tell application \"Slack\" to quit'")
```
## Error Handling
- **App not found:** Search with `mdfind 'kMDItemKind == Application' -name '<name>'`.
- **App crashed:** Use `kill -9` on the process if it's unresponsive.
