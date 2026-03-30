---
name: stock_price
description: Check current stock prices, daily change, and basic market data for specified ticker symbols
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📈"
parameters:
  ticker:
    type: string
    description: "Stock ticker symbol (e.g., AAPL, GOOGL, MSFT)"
required: [ticker]
steps:
  - id: search_stock
    tool: google_search
    args:
      query: "{{ticker}} stock price today 2026"
      num: 3
  - id: present_price
    type: llm
    prompt: "Extract and present the stock information for {{ticker}}:\n\nSearch results:\n{{search_stock.output}}\n\nProvide:\n- Current price\n- Daily change (%)\n- Volume\n- Market cap (if available)\n- Note if market is closed and show last closing price"
    depends_on: [search_stock]
    inputs: [search_stock.output]
---

# Stock Price Check

Get current stock prices.

## Usage

```bash
/stock_price ticker=AAPL
/stock_price ticker=GOOGL
```

## Parameters

- **ticker**: Stock ticker symbol (e.g., AAPL, GOOGL, MSFT) (required)

## Examples

### Check Apple stock

```
ticker=AAPL
```

## Error Handling

- **Invalid ticker:** Suggest checking the ticker symbol.
- **Market closed:** Show last closing price and note market hours.
