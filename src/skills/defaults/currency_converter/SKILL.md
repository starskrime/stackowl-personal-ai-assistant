---
name: currency_converter
description: Convert amounts between currencies using current exchange rates from web search
openclaw:
  emoji: "💱"
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
   run_shell_command("curl -s 'https://api.exchangerate-api.com/v4/latest/<FROM>' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['rates']['<TO>'])\"")
   ```
2. **Calculate and present:** amount, rate, converted amount.
## Examples
### USD to EUR
```
web_search query="100 USD to EUR"
```
## Error Handling
- **Invalid currency code:** Suggest correct ISO 4217 code.
- **API unavailable:** Fall back to web search.
