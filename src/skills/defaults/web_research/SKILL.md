---
name: web_research
description: Perform deep multi-source research on a topic using web search, article crawling, and synthesis
openclaw:
  emoji: "🔬"
---

# Web Research

Conduct comprehensive research on a topic.

## Steps

1. **Run multiple targeted searches:**
   ```
   web_search query="<topic> overview"
   web_search query="<topic> latest research 2026"
   web_search query="<topic> pros and cons"
   ```
2. **Crawl top 2-3 articles for depth:**
   ```
   web_crawl url="<article_url>"
   ```
   If blocked, fall back to `scrapling_fetch url="<article_url>"`.
3. **Synthesize findings** into a structured report:
   - Executive summary (2-3 sentences)
   - Key findings (bullet points)
   - Different perspectives
   - Sources with URLs
4. **Present** to the user.

## Examples

### Research a technology

```
web_search query="WebAssembly use cases 2026"
web_crawl url="<top_result_url>"
```

## Error Handling

- **No results:** Broaden the query or try different search terms.
- **Paywalled articles:** Use `scrapling_fetch` or find alternative sources.
- **Contradictory sources:** Note the disagreement and present both perspectives.
