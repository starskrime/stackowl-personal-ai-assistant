---
name: currency_converter
description: Convert amounts between currencies using current exchange rates from web search
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💱"
parameters:
  amount:
    type: number
    description: "Amount to convert"
  from_currency:
    type: string
    description: "Source currency code (e.g., USD, EUR)"
  to_currency:
    type: string
    description: "Target currency code (e.g., EUR, GBP)"
required: [amount, from_currency, to_currency]
steps:
  - id: fetch_rate
    tool: ShellTool
    args:
      command: "curl -s 'https://api.exchangerate-api.com/v4/latest/{{from_currency}}' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['rates'].get('{{to_currency}}', 'N/A'))\" 2>/dev/null || echo 'API unavailable'"
      mode: "local"
    timeout_ms: 10000
  - id: convert
    type: llm
    prompt: "Calculate the currency conversion: {{amount}} {{from_currency}} to {{to_currency}}. Exchange rate from API: {{fetch_rate.stdout}}. Show: original amount, exchange rate, converted amount."
    depends_on: [fetch_rate]
    inputs: [fetch_rate.stdout]
---

# Currency Converter

Convert between currencies.

## Steps

1. **Get exchange rate:**
   ```
   web_search query="<amount> <from_currency> to <to_currency>"
   ```
   Or use API:
   ```bash
   curl -s 'https://api.exchangerate-api.com/v4/latest/<FROM>' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['rates']['<TO>'])"
   ```
2. **Calculate and present:** amount, rate, converted amount.

## Examples

### USD to EUR

```
amount=100
from_currency="USD"
to_currency="EUR"
```

## Error Handling

- **Invalid currency code:** Suggest correct ISO 4217 code.
- **API unavailable:** Fall back to web search.
