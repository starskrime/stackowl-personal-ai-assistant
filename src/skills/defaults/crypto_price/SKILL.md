---
name: crypto_price
description: Check current cryptocurrency prices, 24h change, and market cap for specified coins
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🪙"
parameters:
  coin:
    type: string
    description: "Cryptocurrency symbol (e.g., bitcoin, ethereum)"
  vs_currency:
    type: string
    description: "Currency to compare against"
    default: "usd"
required: [coin]
steps:
  - id: fetch_price
    tool: ShellTool
    args:
      command: "curl -s 'https://api.coingecko.com/api/v3/simple/price?ids={{coin}}&vs_currencies={{vs_currency}}&include_24hr_change=true&include_market_cap=true' 2>/dev/null | python3 -m json.tool 2>/dev/null || echo '{\"error\": \"API unavailable\"}'"
      mode: "local"
    timeout_ms: 15000
  - id: parse_price
    type: llm
    prompt: "Parse the cryptocurrency price data for '{{coin}}' and present it clearly with: current price in {{vs_currency}}, 24h change percentage (with up/down indicator), and market cap."
    depends_on: [fetch_price]
    inputs: [fetch_price.stdout]
---

# Crypto Price Check

Get cryptocurrency prices.

## Steps

1. **Fetch price data:**
   ```bash
   curl -s 'https://api.coingecko.com/api/v3/simple/price?ids=<coin>&vs_currencies=usd&include_24hr_change=true' | python3 -m json.tool
   ```
2. **Present:** current price, 24h change, market cap.

## Examples

### Check Bitcoin

```
coin="bitcoin"
```

### Check Ethereum

```
coin="ethereum"
vs_currency="usd"
```

## Error Handling

- **API rate limit:** Fall back to `web_search query="<coin> price today"`.
- **Unknown coin:** Search CoinGecko for the correct ID.
