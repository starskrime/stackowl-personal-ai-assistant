---
name: create_reminder
description: Set a timed reminder that triggers a macOS notification after a specified delay
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⏰"
  os: [darwin]
parameters:
  message:
    type: string
    description: "The reminder message"
  delay_seconds:
    type: number
    description: "Delay in seconds (e.g., 1800 for 30 minutes)"
required: [message, delay_seconds]
steps:
  - id: schedule_reminder
    tool: ShellTool
    args:
      command: "(sleep {{delay_seconds}} && osascript -e 'display notification \"{{message}}\" with title \"StackOwl Reminder\"') &"
      mode: "local"
    timeout_ms: 5000
  - id: calculate_trigger_time
    tool: ShellTool
    args:
      command: "date -v+{{delay_seconds}}S '+%H:%M:%S on %Y-%m-%d'"
      mode: "local"
    timeout_ms: 5000
  - id: present_confirmation
    type: llm
    prompt: "Confirm the reminder was set. Message: '{{message}}'. Will trigger at approximately: {{calculate_trigger_time.stdout}}. Reminder will work as long as this terminal session is active."
    depends_on: [schedule_reminder, calculate_trigger_time]
    inputs: [calculate_trigger_time.stdout]
---

# Create Reminder

Set a timed reminder using macOS notification system.

## Steps

1. **Parse the user's request** for:
   - Reminder message (what to remind about)
   - Delay time (e.g., "in 30 minutes", "in 2 hours")

2. **Convert the delay to seconds:**

   ```bash
   echo $((30 * 60))
   ```

3. **Schedule the reminder using a background process:**

   ```bash
   (sleep <seconds> && osascript -e 'display notification "<message>" with title "StackOwl Reminder"') &
   ```

4. **Confirm to the user** with the exact trigger time:
   ```bash
   date -v+<seconds>S '+%H:%M:%S'
   ```

## Examples

### Remind in 30 minutes

```
message="Time to stretch!"
delay_seconds=1800
```

### Remind in 2 hours

```
message="Check the oven"
delay_seconds=7200
```

## Error Handling

- **Invalid time format:** Parse natural language ("30 min", "2h", "1 hour") and convert to seconds. Ask user to clarify if ambiguous.
- **osascript fails:** Fall back to `say` command: `(sleep <s> && say '<message>') &`
- **Very long delays (>24h):** Warn user that reminders survive only while the terminal session is active. Suggest using `at` command or Calendar.app instead.
