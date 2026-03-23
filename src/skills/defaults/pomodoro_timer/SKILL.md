---
name: pomodoro_timer
description: Start a Pomodoro focus timer with configurable work and break intervals and macOS notifications
openclaw:
  emoji: "🍅"
  os: [darwin]
---

# Pomodoro Timer

Run a Pomodoro focus session with notifications.

## Steps

1. **Confirm session parameters** (defaults: 25 min work, 5 min break):
   - Work duration (default: 25 minutes)
   - Break duration (default: 5 minutes)

2. **Start the work timer:**
   ```bash
   run_shell_command("(sleep 1500 && osascript -e 'display notification \"Work session complete! Time for a break.\" with title \"🍅 Pomodoro\" sound name \"Glass\"') &")
   ```

3. **Notify the user** that the Pomodoro session has started with the end time:
   ```bash
   run_shell_command("date -v+25M '+%H:%M'")
   ```

4. **After work timer fires**, prompt user to start break timer:
   ```bash
   run_shell_command("(sleep 300 && osascript -e 'display notification \"Break is over! Ready for the next session?\" with title \"🍅 Pomodoro\" sound name \"Ping\"') &")
   ```

## Examples

### Standard 25/5 Pomodoro
```bash
run_shell_command("(sleep 1500 && osascript -e 'display notification \"Time for a break!\" with title \"🍅 Pomodoro\" sound name \"Glass\"') &")
```

### Long 50/10 session
```bash
run_shell_command("(sleep 3000 && osascript -e 'display notification \"Long session done!\" with title \"🍅 Pomodoro\" sound name \"Glass\"') &")
```

## Error Handling

- **osascript not available:** Use `say "Pomodoro complete"` as audio fallback.
- **User wants to cancel:** Kill background sleep: `run_shell_command("pkill -f 'sleep.*Pomodoro'")`.
