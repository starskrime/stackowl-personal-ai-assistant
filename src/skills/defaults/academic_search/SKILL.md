---
name: academic_search
description: Search for academic papers, journals, and research publications on a given topic
openclaw:
  emoji: "🎓"
---
# Academic Search
Find academic papers and research.
## Steps
1. **Search academic databases:**
   ```
   web_search query="<topic> site:scholar.google.com"
   web_search query="<topic> research paper 2025 2026"
   web_search query="<topic> arxiv"
   ```
2. **Extract paper details:** title, authors, year, abstract, citation count.
3. **Crawl for abstracts if needed:**
   ```
   web_crawl url="<paper_url>"
   ```
4. **Present results** sorted by relevance/recency.
## Examples
### Search for AI papers
```
web_search query="large language model reasoning arxiv 2026"
```
## Error Handling
- **Paywalled papers:** Look for preprint versions on arxiv or author websites.
- **No results:** Broaden search terms or try different keyword combinations.
