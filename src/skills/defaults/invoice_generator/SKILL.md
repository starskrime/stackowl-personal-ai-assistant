---
name: invoice_generator
description: Generate a professional invoice document with line items, totals, and payment details
openclaw:
  emoji: "🧾"
---

# Invoice Generator

Create professional invoices.

## Steps

1. **Collect details:** your business name, client name, line items (description, qty, rate), payment terms.
2. **Generate invoice markdown:**
   ```markdown
   # INVOICE

   **Invoice #:** INV-<number>
   **Date:** <date>
   **Due:** <due_date>
   **From:** <your_business>
   **To:** <client>
   | Item | Qty | Rate | Amount |
   |------|-----|------|--------|
   | <item> | <qty> | $<rate> | $<amount> |
   | **Total** | | | **$<total>** |
   **Payment:\*\* <payment_details>
   ```
3. **Save** as markdown or convert to PDF.

## Examples

### Create an invoice

```bash
write_file("~/invoices/INV-001.md", "<invoice_content>")
```

## Error Handling

- **Missing fields:** Use sensible defaults and flag for review.
