---
name: generate_outline
description: Create a detailed outline for documents, presentations, or books based on a topic and target audience
openclaw:
  emoji: "🗂️"
---
# Generate Outline
Create structured outlines for documents.
## Steps
1. **Collect requirements:** topic, format (article, presentation, book chapter), depth, audience.
2. **Research if needed:**
   ```
   web_search query="<topic> key subtopics"
   ```
3. **Generate hierarchical outline:**
   ```markdown
   # Title
   ## I. Introduction
      A. Hook / Context
      B. Thesis statement
   ## II. Main Point 1
      A. Sub-point
      B. Evidence / Example
   ## III. Main Point 2
      ...
   ## IV. Conclusion
      A. Summary
      B. Call to Action
   ```
4. **Save outline** if requested.
## Examples
### Create article outline
```
web_search query="machine learning in healthcare key topics"
```
## Error Handling
- **Vague topic:** Ask clarifying questions about scope and audience.
