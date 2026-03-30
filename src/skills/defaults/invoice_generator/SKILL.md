---
name: invoice_generator
description: Generate a professional invoice document with line items, totals, and payment details
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧾"
parameters:
  invoice_number:
    type: string
    description: "Invoice number (e.g., INV-001)"
  client_name:
    type: string
    description: "Client name"
  items:
    type: string
    description: "Line items as description|qty|rate (comma-separated)"
  due_date:
    type: string
    description: "Payment due date"
  payment_details:
    type: string
    description: "Payment instructions"
required: [invoice_number, client_name]
steps:
  - id: create_invoices_dir
    tool: ShellTool
    args:
      command: "mkdir -p ~/invoices"
      mode: "local"
    timeout_ms: 5000
  - id: generate_invoice
    tool: WriteFileTool
    args:
      path: "~/invoices/{{invoice_number}}.md"
      content: "# INVOICE\n\n**Invoice #:** {{invoice_number}}\n**Date:** $(date +%Y-%m-%d)\n**Due:** {{due_date}}\n\n**From:** Your Business\n**To:** {{client_name}}\n\n| Item | Qty | Rate | Amount |\n|------|-----|------|--------|\n| {{items}} |\n\n**Payment:** {{payment_details}}\n"
---

# Invoice Generator

Create professional invoices.

## Usage

```bash
/invoice_generator invoice_number=<num> client_name=<name> items=<items> due_date=<date> payment_details=<details>
```

## Parameters

- **invoice_number**: Invoice number (e.g., INV-001)
- **client_name**: Client name
- **items**: Line items as description|qty|rate (comma-separated)
- **due_date**: Payment due date
- **payment_details**: Payment instructions

## Examples

### Create an invoice

```
invoice_number=INV-001
client_name=Acme Corp
items="Consulting|10|150,Travel|2|200"
due_date=2026-04-15
payment_details=Wire transfer to account XXXX
```

## Error Handling

- **Missing fields:** Use sensible defaults and flag for review.
