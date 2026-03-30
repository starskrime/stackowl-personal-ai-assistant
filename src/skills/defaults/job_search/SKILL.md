---
name: job_search
description: Search for job listings matching specified role, location, and experience level
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💼"
parameters:
  role:
    type: string
    description: "Job role or title"
  location:
    type: string
    description: "Location or remote"
    default: "remote"
  experience_level:
    type: string
    description: "Experience level (e.g., junior, senior, lead)"
required: [role]
steps:
  - id: search_primary
    tool: google_search
    args:
      query: "{{role}} jobs {{location}} {{experience_level}} 2026"
      num: 10
  - id: search_remote
    tool: google_search
    args:
      query: "{{role}} remote jobs hiring now"
      num: 10
  - id: analyze_results
    type: llm
    prompt: "Present the job listings found in a clear format with: title, company, location, salary range (if available), and link. Focus on the most relevant and recent postings."
    depends_on: [search_primary, search_remote]
    inputs: [search_primary.output, search_remote.output]
---

# Job Search

Search for job listings matching criteria.

## Usage

```bash
/job_search role=<role> location=<location> experience_level=<level>
```

## Parameters

- **role**: Job role or title
- **location**: Location or remote (default: remote)
- **experience_level**: Experience level (e.g., junior, senior, lead)

## Examples

### Search for engineering roles

```
role=senior software engineer
location=remote
experience_level=senior
```

## Error Handling

- **No results:** Broaden search terms or try related job titles.
