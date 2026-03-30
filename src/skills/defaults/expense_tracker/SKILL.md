---
name: expense_tracker
description: Log and categorize personal expenses to a CSV file with monthly spending summaries
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💸"
parameters:
  action:
    type: string
    description: "Action: log, summary, or total"
    default: "log"
  category:
    type: string
    description: "Expense category (e.g., Food, Transport, Entertainment)"
  description:
    type: string
    description: "Description of the expense"
  amount:
    type: number
    description: "Expense amount"
required: [action]
steps:
  - id: init_file
    tool: ShellTool
    args:
      command: "test -f ~/stackowl_expenses.csv || echo 'date,category,description,amount' > ~/stackowl_expenses.csv"
      mode: "local"
    timeout_ms: 5000
  - id: log_expense
    tool: ShellTool
    args:
      command: "echo '$(date +%Y-%m-%d),{{category}},{{description}},{{amount}}' >> ~/stackowl_expenses.csv"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: monthly_summary
    tool: ShellTool
    args:
      command: "grep '$(date +%Y-%m)' ~/stackowl_expenses.csv | awk -F',' '{sum[$2]+=$4} END {for(c in sum) printf \"%-15s $%.2f\\n\", c, sum[c]}' | sort"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: total_spending
    tool: ShellTool
    args:
      command: "grep '$(date +%Y-%m)' ~/stackowl_expenses.csv | awk -F',' '{sum+=$4} END {printf \"Total: $%.2f\\n\", sum}'"
      mode: "local"
    timeout_ms: 10000
    optional: true
---

# Expense Tracker

Track personal expenses in a local CSV.

## Usage

```bash
/expense_tracker action=<log|summary|total> category=<cat> description=<desc> amount=<amt>
```

## Parameters

- **action**: Action: log, summary, or total (default: log)
- **category**: Expense category (e.g., Food, Transport, Entertainment)
- **description**: Description of the expense
- **amount**: Expense amount

## Examples

### Log a purchase

```
action=log
category=Food
description=Lunch at deli
amount=15.50
```

### View monthly summary

```
action=summary
```

### Show total spending

```
action=total
```

## Error Handling

- **File doesn't exist:** Create with header: `echo 'date,category,description,amount' > ~/stackowl_expenses.csv`
- **Invalid amount:** Validate it's a number before logging.
