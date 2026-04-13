---
name: price_compare
description: Compare prices for a product across multiple online retailers using web search
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💰"
parameters:
  product:
    type: string
    description: "The product name to search for"
required: [product]
steps:
  - id: search_amazon
    tool: duckduckgo_search
    args:
      query: "{{product}} price Amazon"
      num: 5
  - id: search_bestbuy
    tool: duckduckgo_search
    args:
      query: "{{product}} price Best Buy"
      num: 5
  - id: search_walmart
    tool: duckduckgo_search
    args:
      query: "{{product}} price Walmart"
      num: 5
  - id: compare_prices
    type: llm
    prompt: "Create a price comparison table from the search results for {{product}}. Extract prices from Amazon, Best Buy, and Walmart. Highlight the best deal."
    depends_on: [search_amazon, search_bestbuy, search_walmart]
    inputs: [search_amazon.output, search_bestbuy.output, search_walmart.output]
---

# Price Compare

Compare prices for a product across retailers.

## Usage

```bash
/price_compare product="MacBook Air M3"
```

## Parameters

- **product**: The product name to search for (required)

## Examples

### Compare laptop prices

```
product="MacBook Air M3 2026"
```

## Error Handling

- **Price not found:** Note "Price unavailable" for that retailer.
- **Out of stock:** Indicate stock status when available.
