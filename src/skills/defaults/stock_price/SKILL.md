---
name: stock_price
description: Check current stock prices, daily change, and basic market data for specified ticker symbols
openclaw:
  emoji: "📈"
---
# Stock Price Check
Get current stock prices.
## Steps
1. **Search for stock price:**
   ```
   web_search query="<ticker> stock price today"
   ```
2. **Extract:** current price, daily change (%), volume, market cap.
3. **Present** formatted summary.
## Examples
### Check Apple stock
```
web_search query="AAPL stock price today"
```
## Error Handling
- **Invalid ticker:** Suggest checking the ticker symbol.
- **Market closed:** Show last closing price and note market hours.
