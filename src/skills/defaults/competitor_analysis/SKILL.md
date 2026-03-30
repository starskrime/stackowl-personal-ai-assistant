---
name: competitor_analysis
description: Research and analyze competitor products, features, pricing, and market positioning
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🏢"
parameters:
  product:
    type: string
    description: "The product or business to analyze competitors for"
  competitors:
    type: string
    description: "Known competitor names (comma-separated, optional)"
    default: ""
required: [product]
steps:
  - id: find_competitors
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{product}}+competitors+alternatives+2026&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r['Text']) for r in d.get('RelatedTopics',[])[:10]]\" 2>/dev/null || echo 'Search unavailable'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: research_competitors
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{product}}+pricing+features+review+2026&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r['Text']) for r in d.get('RelatedTopics',[])[:15]]\" 2>/dev/null || echo 'Research unavailable'"
      mode: "local"
    timeout_ms: 15000
  - id: analyze
    type: llm
    prompt: "Create a competitor analysis for '{{product}}'. Competitors to analyze: {{competitors}}. Based on research:\n{{research_competitors.stdout}}\n\nBuild a comparison table with: competitor name, key features, pricing model, pros/cons, and market positioning. Provide strategic insights."
    depends_on: [research_competitors]
    inputs: [research_competitors.stdout]
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
product="Notion alternatives"
```

## Error Handling

- **No competitor data:** Note gaps and suggest manual research.
