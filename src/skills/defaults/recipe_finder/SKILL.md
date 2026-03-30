---
name: recipe_finder
description: Find recipes based on available ingredients, dietary restrictions, or cuisine preferences
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🍳"
parameters:
  ingredients:
    type: string
    description: "Available ingredients (comma-separated)"
  diet:
    type: string
    description: "Dietary restriction (vegan, vegetarian, gluten-free, dairy-free, etc.)"
    default: ""
  cuisine:
    type: string
    description: "Preferred cuisine type (Italian, Mexican, Asian, etc.)"
    default: ""
  max_time:
    type: number
    description: "Maximum cooking time in minutes (0 for any)"
    default: 0
required: [ingredients]
steps:
  - id: search_recipes
    tool: google_search
    args:
      query: "recipe with {{ingredients}} {{diet}} {{cuisine}} {{if(max_time > 0, 'under ' + max_time + ' minutes', '')}}"
      num: 5
  - id: find_recipe_urls
    type: llm
    prompt: "From the search results, identify 2-3 promising recipe URLs. Return ONLY the URLs separated by newlines.\n\nSearch results: {{search_recipes.output}}"
    depends_on: [search_recipes]
    inputs: [search_recipes.output]
  - id: crawl_first_recipe
    tool: WebCrawlTool
    args:
      url: "{{find_recipe_urls.output.split('\n')[0]}}"
    optional: true
    depends_on: [find_recipe_urls]
  - id: present_recipe
    type: llm
    prompt: "Present a formatted recipe with ingredients list, cooking steps, prep time, cook time, and serving size based on the crawled content.\n\nRecipe: {{crawl_first_recipe.output}}"
    depends_on: [crawl_first_recipe]
    inputs: [crawl_first_recipe.output]
---

# Recipe Finder

Find recipes matching user's ingredients or preferences.

## Usage

```bash
/recipe_finder ingredients="chicken, garlic, lemon"
/recipe_finder ingredients="tofu, rice, vegetables" diet=vegan max_time=30
```

## Parameters

- **ingredients**: Available ingredients (comma-separated) (required)
- **diet**: Dietary restriction (vegan, vegetarian, gluten-free, etc.) (default: none)
- **cuisine**: Preferred cuisine type (Italian, Mexican, Asian, etc.) (default: none)
- **max_time**: Maximum cooking time in minutes (default: 0 = any)

## Examples

### Find chicken recipes

```
ingredients="chicken, garlic, lemon"
```

## Error Handling

- **No recipes found:** Broaden ingredient list or relax dietary restrictions.
- **Recipe page blocked:** Try alternative recipes from search results.
