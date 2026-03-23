---
name: competitor_analysis
description: Research and analyze competitor products, features, pricing, and market positioning
openclaw:
  emoji: "🏢"
---
# Competitor Analysis
Research competitors for a given product or business.
## Steps
1. **Identify competitor names** from user or search:
   ```
   web_search query="<product> competitors alternatives 2026"
   ```
2. **Research each competitor:**
   ```
   web_search query="<competitor> pricing features review"
   web_crawl url="<competitor_website>"
   ```
3. **Build comparison table:** features, pricing, pros/cons.
4. **Present analysis** with strategic insights.
## Examples
```
web_search query="Notion alternatives comparison 2026"
```
## Error Handling
- **No competitor data:** Note gaps and suggest manual research.
