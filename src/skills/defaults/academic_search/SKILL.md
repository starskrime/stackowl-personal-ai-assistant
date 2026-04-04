---
name: academic_search
description: Search for academic papers, journals, and research publications on a given topic
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎓"
parameters:
  topic:
    type: string
    description: "The research topic to search for"
  search_type:
    type: string
    description: "Type of search: scholar, arxiv, general"
    default: "general"
required: [topic]
steps:
  - id: search_arxiv
    tool: ShellTool
    args:
      command: "curl -s 'https://export.arxiv.org/api/query?search_query=ti:{{topic}}&max_results=10' 2>/dev/null | grep -E '<title>|<id>|<published>' | head -30"
    mode: "local"
    timeout_ms: 15000
    optional: true
  - id: search_scholar
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{topic}}+academic+research+pdf&format=json' 2>/dev/null | head -100"
    mode: "local"
    timeout_ms: 15000
    optional: true
  - id: web_search
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{topic}}+research+papers+2025+2026&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r.get('Text','')) for r in d.get('RelatedTopics',[])[:10]]\""
    mode: "local"
    timeout_ms: 15000
  - id: present_results
    type: llm
    prompt: "Summarize academic search results for '{{topic}}'. Format as a list with paper titles, sources, and relevance notes."
    depends_on: [web_search]
    inputs: [web_search.output]
---

# Academic Search

Find academic papers and research publications.

## Usage

```bash
/academic_search "machine learning"
```

With search type:
```
topic=neural networks
search_type=arxiv
```

## Parameters

- **topic**: Research topic to search for (required)
- **search_type**: scholar, arxiv, or general (default: general)

## Examples

### AI research papers
```
topic="large language model reasoning"
```

### Machine learning
```
topic=reinforcement learning
search_type=general
```

## Notes

- Searches multiple academic sources
- Returns paper titles and sources
- Use web_crawl to fetch full papers