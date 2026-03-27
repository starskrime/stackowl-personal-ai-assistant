---
name: budget_planner
description: Create a monthly budget plan based on income, fixed expenses, and savings goals
openclaw:
  emoji: "💵"
---

# Budget Planner

Create a monthly budget.

## Steps

1. **Collect financial info:** monthly income, fixed expenses, savings goal.
2. **Calculate budget allocation** (50/30/20 or custom):
   - 50% Needs (rent, bills, groceries)
   - 30% Wants (dining, entertainment)
   - 20% Savings / debt repayment
3. **Format budget:**
   ```markdown
   ## Monthly Budget — March 2026

   **Income:** $X,XXX

   ### Needs (50% = $X,XXX)

   - Rent: $X,XXX
   - Utilities: $XXX

   ### Wants (30% = $X,XXX)

   - Dining: $XXX

   ### Savings (20% = $X,XXX)

   - Emergency fund: $XXX
   ```
4. **Save** to file if requested.

## Examples

### Create a $5000/month budget

```markdown
Income: $5,000
Needs: $2,500 | Wants: $1,500 | Savings: $1,000
```

## Error Handling

- **Expenses exceed income:** Flag and suggest adjustments.
