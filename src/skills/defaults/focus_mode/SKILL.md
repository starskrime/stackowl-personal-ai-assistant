---
name: focus_mode
description: Enable system-wide focus mode on macOS by activating Do Not Disturb and optionally blocking distracting apps
openclaw:
  emoji: "🧘"
  os: [darwin]
---

# Focus Mode

Enable macOS Do Not Disturb and optionally block distracting applications.

## Steps

1. **Enable Do Not Disturb:**
   ```bash
   run_shell_command("shortcuts run 'Set Focus' 2>/dev/null || osascript -e 'do shell script \"defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean true && killall NotificationCenter\"'")
   ```

2. **Optionally block distracting apps** (quit them):
   ```bash
   run_shell_command("osascript -e 'tell application \"<app_name>\" to quit'")
   ```
   Common distracting apps: Slack, Discord, Twitter, Messages.

3. **Set a timer to disable focus mode:**
   ```bash
   run_shell_command("(sleep <seconds> && osascript -e 'display notification \"Focus session ended\" with title \"🧘 Focus Mode\"') &")
   ```

4. **Confirm** focus mode is active and when it will end.

## Examples

### 1-hour focus session
```bash
run_shell_command("osascript -e 'tell application \"Slack\" to quit'")
run_shell_command("osascript -e 'tell application \"Discord\" to quit'")
```

## Error Handling

- **DND command fails:** Inform user to enable Focus manually in System Settings.
- **App not running:** Silently skip—no error needed.
