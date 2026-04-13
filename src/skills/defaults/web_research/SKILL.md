---
name: web_research
description: Perform deep multi-source research on a topic using web search, article crawling, and synthesis
command-dispatch: tool
command-tool: duckduckgo_search
openclaw:
  emoji: "🔬"
parameters:
  topic:
    type: string
    description: "Research topic or question"
required: [topic]
steps:
  - id: search_overview
    tool: duckduckgo_search
    args:
      query: "{{topic}} overview"
    timeout_ms: 15000
  - id: search_latest
    tool: duckduckgo_search
    args:
      query: "{{topic}} latest research 2026"
    timeout_ms: 15000
  - id: search_proscons
    tool: duckduckgo_search
    args:
      query: "{{topic}} pros and cons"
    timeout_ms: 15000
  - id: crawl_article
    tool: WebCrawlTool
    args:
      url: "{{article_url}}"
    timeout_ms: 30000
    optional: true
  - id: synthesize
    type: llm
    prompt: "Synthesize comprehensive research findings on '{{topic}}' into a structured report with:\n\n1. Executive Summary (2-3 sentences)\n2. Key Findings (bullet points)\n3. Different Perspectives\n4. Sources with URLs\n\nSearch results overview: {{search_overview.output}}\nLatest research: {{search_latest.output}}\nPros and cons: {{search_proscons.output}}\n{{#if crawl_article.output}}Crawled article: {{crawl_article.output}}{{/if}}"
    depends_on: [search_overview, search_latest, search_proscons, crawl_article]
    inputs:
      [
        search_overview.output,
        search_latest.output,
        search_proscons.output,
        crawl_article.output,
      ]
---

# Web Research

Conduct comprehensive research on a topic.

## Usage

```bash
/web_research topic="WebAssembly use cases"
```

## Parameters

- **topic**: Research topic or question

## Examples

```
web_research topic="WebAssembly use cases 2026"
```

## Error Handling

- **No results:** Broaden the query or try different search terms.
- **Paywalled articles:** Use `scrapling_fetch` or find alternative sources.
- **Contradictory sources:** Note the disagreement and present both perspectives.
