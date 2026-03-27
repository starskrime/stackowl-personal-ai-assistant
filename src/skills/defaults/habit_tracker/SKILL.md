---
name: habit_tracker
description: Track daily habits by logging completions to a CSV file and showing weekly streaks
openclaw:
  emoji: "📊"
---

# Habit Tracker

Track daily habits using a local CSV file at `~/stackowl_habits.csv`.

## Steps

1. **Read current habits file:**

   ```bash
   run_shell_command("cat ~/stackowl_habits.csv 2>/dev/null || echo 'date,habit,completed'")
   ```

2. **Perform the requested action:**

   **Log a habit completion:**

   ```bash
   run_shell_command("echo '$(date +%Y-%m-%d),<habit_name>,true' >> ~/stackowl_habits.csv")
   ```

   **Show streak for a habit:**

   ```bash
   run_shell_command("grep '<habit_name>' ~/stackowl_habits.csv | tail -7")
   ```

   **List all tracked habits:**

   ```bash
   run_shell_command("cut -d',' -f2 ~/stackowl_habits.csv | sort -u | grep -v habit")
   ```

3. **Present a summary** showing current streaks and completion rates.

## Examples

### Log exercise habit

```bash
run_shell_command("echo '2026-03-22,exercise,true' >> ~/stackowl_habits.csv")
```

### View weekly summary

```bash
run_shell_command("tail -30 ~/stackowl_habits.csv | grep 'true' | cut -d',' -f2 | sort | uniq -c | sort -rn")
```

## Error Handling

- **File doesn't exist:** Create with header: `echo 'date,habit,completed' > ~/stackowl_habits.csv`
- **Duplicate entry for today:** Check before logging: `grep "$(date +%Y-%m-%d),<habit>" ~/stackowl_habits.csv`
