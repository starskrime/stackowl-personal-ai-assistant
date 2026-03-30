---
name: weekly_review
description: Generate a weekly productivity review summarizing completed tasks, habits, and key accomplishments
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📋"
parameters:
  todo_file:
    type: string
    description: "Path to todo file"
    default: "~/stackowl_todos.md"
  habits_file:
    type: string
    description: "Path to habits CSV file"
    default: "~/stackowl_habits.csv"
required: []
steps:
  - id: get_week_range
    tool: ShellTool
    args:
      command: "echo \"Week of $(date -v-7d '+%B %d') to $(date '+%B %d, %Y')\""
      mode: "local"
    timeout_ms: 5000
  - id: get_completed_tasks
    tool: ShellTool
    args:
      command: "grep '\\[x\\]' {{todo_file}} 2>/dev/null || echo 'No completed tasks found'"
      mode: "local"
    timeout_ms: 5000
  - id: get_habit_streaks
    tool: ShellTool
    args:
      command: "tail -50 {{habits_file}} 2>/dev/null | grep 'true' | cut -d',' -f2 | sort | uniq -c | sort -rn || echo 'No habit data found'"
      mode: "local"
    timeout_ms: 5000
  - id: compose_review
    type: llm
    prompt: "Generate a weekly productivity review for {{get_week_range.output}} based on:\n\nCompleted tasks:\n{{get_completed_tasks.output}}\n\nHabit streaks:\n{{get_habit_streaks.output}}\n\nFormat as markdown with sections:\n- Week date range\n- Completed Tasks (checklist format)\n- Habit Summary (X/7 days format)\n- Key Wins\n- Focus for Next Week"
    depends_on: [get_week_range, get_completed_tasks, get_habit_streaks]
    inputs: [get_week_range.output, get_completed_tasks.output, get_habit_streaks.output]
---

# Weekly Review

Generate a structured weekly review from todos, habits, and pellets.

## Usage

```bash
/weekly_review
```

## Parameters

- **todo_file**: Path to todo file (default: ~/stackowl_todos.md)
- **habits_file**: Path to habits CSV file (default: ~/stackowl_habits.csv)

## Error Handling

- **No todo file found:** Report "No task tracking data available" and offer to set it up.
- **No habits tracked:** Skip habits section and note it in the review.
