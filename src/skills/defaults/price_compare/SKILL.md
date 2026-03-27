---
name: price_compare
description: Compare prices for a product across multiple online retailers using web search
openclaw:
  emoji: "💰"
---

# Price Compare

Compare prices for a product across retailers.

## Steps

1. **Search for the product on multiple retailers:**
   ```
   web_search query="<product> price Amazon"
   web_search query="<product> price Best Buy"
   web_search query="<product> price Walmart"
   ```
2. **Extract prices** from search results.
3. **Present a comparison table:**
   | Retailer | Price | Link |
   |----------|-------|------|
   | Amazon | $XX | url |
   | Best Buy | $XX | url |
4. **Highlight the best deal.**

## Examples

### Compare laptop prices

```
web_search query="MacBook Air M3 price Amazon 2026"
web_search query="MacBook Air M3 price Best Buy 2026"
```

## Error Handling

- **Price not found:** Note "Price unavailable" for that retailer.
- **Out of stock:** Indicate stock status when available.
