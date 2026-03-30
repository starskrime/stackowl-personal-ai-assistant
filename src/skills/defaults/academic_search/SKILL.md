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
  - id: search_scholar
    tool: ShellTool
    args:
      command: "curl -s -G 'https://scholar.google.com/scholar' --data-urlencode 'q={{topic}}' 2>/dev/null | grep -oP '(?<=<h3 class="gs_rt">)[^<]+' | head -10 || echo 'Google Scholar search unavailable'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: search_arxiv
    tool: ShellTool
    args:
      command: "curl -s 'https://export.arxiv.org/api/query?search_query=ti:{{topic}}&max_results=10' 2>/dev/null | grep -E '<title>|<id>|<published>' | head -30 || echo 'ArXiv search unavailable'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: web_search
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{topic}}+research+papers+2025+2026&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r['Text']) for r in d.get('RelatedTopics',[])[:10]]\" 2>/dev/null || echo 'Web search unavailable'"
      mode: "local"
    timeout_ms: 15000
  - id: present_results
    type: llm
    prompt: "Summarize the academic search results for '{{topic}}'. Format as a list with paper titles, sources, and relevance notes."
    depends_on: [web_search]
    inputs: [web_search.stdout]
---

# Academic Search

Find academic papers and research.

## Steps

1. **Search academic databases:**
   - Search Google Scholar for peer-reviewed papers
   - Search arXiv for preprint papers
   - Use web search for recent research
2. **Extract paper details:** title, authors, year, abstract, citation count.
3. **Crawl for abstracts if needed:**
   ```
   web_crawl url="<paper_url>"
   ```
4. **Present results** sorted by relevance/recency.

## Examples

### Search for AI papers

```
topic="large language model reasoning"
```

## Error Handling

- **Paywalled papers:** Look for preprint versions on arxiv or author websites.
- **No results:** Broaden search terms or try different keyword combinations.
