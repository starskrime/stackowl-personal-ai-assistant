---
name: crypto_price
description: Check current cryptocurrency prices, 24h change, and market cap for specified coins
openclaw:
  emoji: "🪙"
---

# Crypto Price Check

Get cryptocurrency prices.

## Steps

1. **Fetch price data:**
   ```bash
   run_shell_command("curl -s 'https://api.coingecko.com/api/v3/simple/price?ids=<coin>&vs_currencies=usd&include_24hr_change=true' | python3 -m json.tool")
   ```
2. **Present:** current price, 24h change, market cap.

## Examples

### Check Bitcoin

```bash
run_shell_command("curl -s 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true'")
```

## Error Handling

- **API rate limit:** Fall back to `web_search query="<coin> price today"`.
- **Unknown coin:** Search CoinGecko for the correct ID.
