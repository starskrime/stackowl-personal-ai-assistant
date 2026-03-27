---
name: recipe_finder
description: Find recipes based on available ingredients, dietary restrictions, or cuisine preferences
openclaw:
  emoji: "🍳"
---

# Recipe Finder

Find recipes matching user's ingredients or preferences.

## Steps

1. **Collect user preferences:**
   - Available ingredients
   - Dietary restrictions (vegan, gluten-free, etc.)
   - Cuisine preference
   - Time constraint
2. **Search for recipes:**
   ```
   web_search query="recipe with <ingredients> <diet> <cuisine>"
   ```
3. **Crawl top recipe page:**
   ```
   web_crawl url="<recipe_url>"
   ```
4. **Present recipe** with ingredients list, steps, cook time, and serving size.

## Examples

### Find chicken recipes

```
web_search query="easy chicken dinner recipe under 30 minutes"
```

## Error Handling

- **No recipes found:** Broaden ingredient list or relax dietary restrictions.
- **Recipe page blocked:** Try `scrapling_fetch` or search for alternative recipes.
