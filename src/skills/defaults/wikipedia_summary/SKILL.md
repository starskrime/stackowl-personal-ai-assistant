---
name: wikipedia_summary
description: Fetch and summarize a Wikipedia article on a given topic with key facts and context
command-dispatch: tool
command-tool: google_search
openclaw:
  emoji: "📚"
parameters:
  topic:
    type: string
    description: "Topic to search on Wikipedia"
required: [topic]
steps:
  - id: search_wikipedia
    tool: google_search
    args:
      query: "{{topic}} site:en.wikipedia.org"
    timeout_ms: 15000
  - id: crawl_article
    tool: WebCrawlTool
    args:
      url: "https://en.wikipedia.org/wiki/{{topic}}"
    timeout_ms: 30000
  - id: summarize
    type: llm
    prompt: "Extract and summarize key facts from this Wikipedia article on '{{topic}}'. Provide:\n- 3-5 paragraph summary with dates and context\n- Key facts as bullet points\n- Note if this is a disambiguation page and which article was chosen\n\nArticle content: {{crawl_article.output}}\nSearch results: {{search_wikipedia.output}}"
    depends_on: [search_wikipedia, crawl_article]
    inputs: [crawl_article.output, search_wikipedia.output]
---

# Wikipedia Summary

Get quick summaries from Wikipedia.

## Usage

```bash
/wikipedia_summary topic="Quantum computing"
```

## Parameters

- **topic**: Topic to search on Wikipedia

## Examples

```
wikipedia_summary topic="Quantum computing"
```

## Error Handling

- **Disambiguation page:** Pick the most relevant article and note alternatives.
- **Article doesn't exist:** Search for related topics.
