---
name: habit_tracker
description: Track daily habits by logging completions to a CSV file and showing weekly streaks
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📊"
parameters:
  action:
    type: string
    description: "Action: log, streak, or list"
    default: "log"
  habit_name:
    type: string
    description: "Name of the habit to track"
required: [action]
steps:
  - id: init_file
    tool: ShellTool
    args:
      command: "test -f ~/stackowl_habits.csv || echo 'date,habit,completed' > ~/stackowl_habits.csv"
      mode: "local"
    timeout_ms: 5000
  - id: log_habit
    tool: ShellTool
    args:
      command: "echo '$(date +%Y-%m-%d),{{habit_name}},true' >> ~/stackowl_habits.csv"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: show_streak
    tool: ShellTool
    args:
      command: "grep '{{habit_name}}' ~/stackowl_habits.csv | tail -7"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: list_habits
    tool: ShellTool
    args:
      command: "cut -d',' -f2 ~/stackowl_habits.csv | sort -u | grep -v habit"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: weekly_summary
    tool: ShellTool
    args:
      command: "tail -30 ~/stackowl_habits.csv | grep 'true' | cut -d',' -f2 | sort | uniq -c | sort -rn"
      mode: "local"
    timeout_ms: 10000
    optional: true
---

# Habit Tracker

Track daily habits using a local CSV file at `~/stackowl_habits.csv`.

## Usage

```bash
/habit_tracker action=<log|streak|list> habit_name=<name>
```

## Parameters

- **action**: Action: log, streak, or list (default: log)
- **habit_name**: Name of the habit to track

## Examples

### Log exercise habit

```
action=log
habit_name=exercise
```

### View streak for a habit

```
action=streak
habit_name=exercise
```

## Error Handling

- **File doesn't exist:** Create with header: `echo 'date,habit,completed' > ~/stackowl_habits.csv`
- **Duplicate entry for today:** Check before logging: `grep "$(date +%Y-%m-%d),<habit>" ~/stackowl_habits.csv`
