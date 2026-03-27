---
name: wikipedia_summary
description: Fetch and summarize a Wikipedia article on a given topic with key facts and context
openclaw:
  emoji: "📚"
---

# Wikipedia Summary

Get quick summaries from Wikipedia.

## Steps

1. **Search Wikipedia:**
   ```
   web_search query="<topic> site:en.wikipedia.org"
   ```
2. **Crawl the article:**
   ```
   web_crawl url="https://en.wikipedia.org/wiki/<topic>"
   ```
   If blocked: `scrapling_fetch url="<url>"`
3. **Extract and summarize:** key facts, dates, context (3-5 paragraphs).

## Examples

### Summarize a topic

```
web_search query="Quantum computing site:en.wikipedia.org"
web_crawl url="https://en.wikipedia.org/wiki/Quantum_computing"
```

## Error Handling

- **Disambiguation page:** Pick the most relevant article and note alternatives.
- **Article doesn't exist:** Search for related topics.
