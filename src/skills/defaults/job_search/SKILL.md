---
name: job_search
description: Search for job listings matching specified role, location, and experience level
openclaw:
  emoji: "💼"
---

# Job Search

Search for job listings matching criteria.

## Steps

1. **Collect search criteria:**
   - Role/title
   - Location (or remote)
   - Experience level
   - Salary range
2. **Search job boards:**
   ```
   web_search query="<role> jobs <location> <experience_level> 2026"
   web_search query="<role> remote jobs hiring now"
   ```
3. **Present listings** with: title, company, location, salary range, link.

## Examples

### Search for engineering roles

```
web_search query="senior software engineer remote jobs 2026"
```

## Error Handling

- **No results:** Broaden search terms or try related job titles.
