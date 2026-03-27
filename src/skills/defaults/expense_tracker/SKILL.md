---
name: expense_tracker
description: Log and categorize personal expenses to a CSV file with monthly spending summaries
openclaw:
  emoji: "💸"
---

# Expense Tracker

Track personal expenses in a local CSV.

## Steps

1. **Log an expense:**
   ```bash
   run_shell_command("echo '$(date +%Y-%m-%d),<category>,<description>,<amount>' >> ~/stackowl_expenses.csv")
   ```
2. **View monthly summary:**
   ```bash
   run_shell_command("grep '$(date +%Y-%m)' ~/stackowl_expenses.csv | awk -F',' '{sum[$2]+=$4} END {for(c in sum) printf \"%-15s $%.2f\\n\", c, sum[c]}' | sort")
   ```
3. **Show total spending:**
   ```bash
   run_shell_command("grep '$(date +%Y-%m)' ~/stackowl_expenses.csv | awk -F',' '{sum+=$4} END {printf \"Total: $%.2f\\n\", sum}'")
   ```

## Examples

### Log a purchase

```bash
run_shell_command("echo '2026-03-22,Food,Lunch at deli,15.50' >> ~/stackowl_expenses.csv")
```

## Error Handling

- **File doesn't exist:** Create with header: `echo 'date,category,description,amount' > ~/stackowl_expenses.csv`
- **Invalid amount:** Validate it's a number before logging.
