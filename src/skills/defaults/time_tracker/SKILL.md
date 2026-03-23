---
name: time_tracker
description: Track time spent on tasks by starting and stopping a timer, with daily summaries logged to a file
openclaw:
  emoji: "⏱️"
---

# Time Tracker

Track time spent on tasks using start/stop timestamps in a log file.

## Steps

1. **Start tracking:**
   ```bash
   run_shell_command("echo \"START,$(date +%Y-%m-%dT%H:%M:%S),<task_name>\" >> ~/stackowl_timetrack.csv")
   ```

2. **Stop tracking:**
   ```bash
   run_shell_command("echo \"STOP,$(date +%Y-%m-%dT%H:%M:%S),<task_name>\" >> ~/stackowl_timetrack.csv")
   ```

3. **Calculate duration** between START and STOP for a task:
   ```bash
   run_shell_command("grep '<task_name>' ~/stackowl_timetrack.csv")
   ```

4. **Show daily summary** of all tracked time.

## Examples

### Start timer
```bash
run_shell_command("echo \"START,$(date +%Y-%m-%dT%H:%M:%S),coding\" >> ~/stackowl_timetrack.csv")
```

### Stop timer
```bash
run_shell_command("echo \"STOP,$(date +%Y-%m-%dT%H:%M:%S),coding\" >> ~/stackowl_timetrack.csv")
```

## Error Handling

- **Stop without start:** Warn user that no active timer was found for that task.
- **File doesn't exist:** Create with header row.
- **Multiple active timers:** List them and ask which to stop.
