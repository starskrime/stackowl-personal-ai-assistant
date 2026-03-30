---
name: trending_topics
description: Discover what topics are currently trending on social media, news, and search engines
command-dispatch: tool
command-tool: google_search
openclaw:
  emoji: "📈"
parameters:
  region:
    type: string
    description: "Region for trending topics (e.g., US, Global)"
    default: "Global"
  category:
    type: string
    description: "Category filter (news, technology, entertainment, sports)"
    default: "all"
required: []
steps:
  - id: search_trending
    tool: google_search
    args:
      query: "trending topics today"
    timeout_ms: 15000
  - id: search_twitter
    tool: google_search
    args:
      query: "what's trending on Twitter today"
    timeout_ms: 15000
  - id: search_google_trends
    tool: google_search
    args:
      query: "Google Trends today"
    timeout_ms: 15000
  - id: categorize_trends
    type: llm
    prompt: "Categorize and present the top 10 trending topics from the search results. Group them by category (News & Politics, Technology, Entertainment, Sports) and provide brief context for each:\n\nGeneral trends: {{search_trending.output}}\nTwitter trends: {{search_twitter.output}}\nGoogle Trends: {{search_google_trends.output}}\n\nUser region: {{region}}\nCategory filter: {{category}}"
    depends_on: [search_trending, search_twitter, search_google_trends]
    inputs: [search_trending.output, search_twitter.output, search_google_trends.output]
---

# Trending Topics

Find currently trending topics.

## Usage

```bash
/trending_topics
/trending_topics region=US category=technology
```

## Parameters

- **region**: Region for trending topics (e.g., US, Global, default: Global)
- **category**: Category filter (news, technology, entertainment, sports, all, default: all)

## Error Handling

- **Region-specific trends:** Ask user for their region if results are too broad.
