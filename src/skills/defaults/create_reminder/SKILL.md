---
name: create_reminder
description: Set a timed reminder that triggers a macOS notification after a specified delay
openclaw:
  emoji: "⏰"
  os: [darwin]
---

# Create Reminder

Set a timed reminder using macOS notification system.

## Steps

1. **Parse the user's request** for:
   - Reminder message (what to remind about)
   - Delay time (e.g., "in 30 minutes", "in 2 hours")

2. **Convert the delay to seconds:**
   ```bash
   run_shell_command("echo $((30 * 60))")
   ```

3. **Schedule the reminder using a background process:**
   ```bash
   run_shell_command("(sleep <seconds> && osascript -e 'display notification \"<message>\" with title \"StackOwl Reminder\"') &")
   ```

4. **Confirm to the user** with the exact trigger time:
   ```bash
   run_shell_command("date -v+<seconds>S '+%H:%M:%S'")
   ```

## Examples

### Remind in 30 minutes
```bash
run_shell_command("(sleep 1800 && osascript -e 'display notification \"Time to stretch!\" with title \"StackOwl Reminder\"') &")
```

### Remind in 2 hours
```bash
run_shell_command("(sleep 7200 && osascript -e 'display notification \"Check the oven\" with title \"StackOwl Reminder\"') &")
```

## Error Handling

- **Invalid time format:** Parse natural language ("30 min", "2h", "1 hour") and convert to seconds. Ask user to clarify if ambiguous.
- **osascript fails:** Fall back to `say` command: `run_shell_command("(sleep <s> && say '<message>') &")`
- **Very long delays (>24h):** Warn user that reminders survive only while the terminal session is active. Suggest using `at` command or Calendar.app instead.
