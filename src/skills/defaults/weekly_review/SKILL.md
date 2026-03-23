---
name: weekly_review
description: Generate a weekly productivity review summarizing completed tasks, habits, and key accomplishments
openclaw:
  emoji: "📋"
---

# Weekly Review

Generate a structured weekly review from todos, habits, and pellets.

## Steps

1. **Get the current week range:**
   ```bash
   run_shell_command("echo \"Week of $(date -v-7d '+%B %d') to $(date '+%B %d, %Y')\"")
   ```

2. **Gather completed tasks:**
   ```bash
   run_shell_command("grep '\\[x\\]' ~/stackowl_todos.md 2>/dev/null || echo 'No completed tasks found'")
   ```

3. **Gather habit streaks:**
   ```bash
   run_shell_command("tail -50 ~/stackowl_habits.csv 2>/dev/null | grep 'true' | cut -d',' -f2 | sort | uniq -c | sort -rn")
   ```

4. **Compose the review** in markdown:
   ```markdown
   # Weekly Review: <date range>

   ## ✅ Completed Tasks
   - <task 1>

   ## 📊 Habit Summary
   - Exercise: 5/7 days
   - Reading: 3/7 days

   ## 🏆 Key Wins
   - <accomplishment>

   ## 🎯 Focus for Next Week
   - <priority 1>
   ```

5. **Save and present** to the user.

## Examples

### Generate weekly review
```bash
run_shell_command("grep '\\[x\\]' ~/stackowl_todos.md")
```

## Error Handling

- **No todo file found:** Report "No task tracking data available" and offer to set it up.
- **No habits tracked:** Skip habits section and note it in the review.
