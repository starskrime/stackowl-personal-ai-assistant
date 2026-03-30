---
name: travel_planner
description: Create a detailed travel itinerary with flights, hotels, attractions, and daily schedules
command-dispatch: tool
command-tool: google_search
openclaw:
  emoji: "✈️"
parameters:
  destination:
    type: string
    description: "Travel destination city or country"
  start_date:
    type: string
    description: "Trip start date"
  end_date:
    type: string
    description: "Trip end date"
  budget:
    type: string
    description: "Budget range (e.g., low, medium, high)"
    default: "medium"
  interests:
    type: string
    description: "Comma-separated interests (culture, nature, food, adventure)"
    default: "culture,food"
required: [destination, start_date, end_date]
steps:
  - id: search_guide
    tool: google_search
    args:
      query: "{{destination}} travel guide 2026"
    timeout_ms: 15000
  - id: search_attractions
    tool: google_search
    args:
      query: "{{destination}} top attractions"
    timeout_ms: 15000
  - id: search_restaurants
    tool: google_search
    args:
      query: "{{destination}} best restaurants"
    timeout_ms: 15000
  - id: search_weather
    tool: google_search
    args:
      query: "{{destination}} weather {{start_date}}"
    timeout_ms: 15000
  - id: build_itinerary
    type: llm
    prompt: "Create a detailed travel itinerary for {{destination}} from {{start_date}} to {{end_date}} with budget level '{{budget}}' and interests in {{interests}}.\n\nUse the research gathered:\n- Travel guide: {{search_guide.output}}\n- Attractions: {{search_attractions.output}}\n- Restaurants: {{search_restaurants.output}}\n- Weather: {{search_weather.output}}\n\nFormat as a daily schedule with morning, afternoon, and evening activities. Include practical info like currency, local transport tips, and estimated costs."
    depends_on: [search_guide, search_attractions, search_restaurants, search_weather]
    inputs: [search_guide.output, search_attractions.output, search_restaurants.output, search_weather.output]
---

# Travel Planner

Create a comprehensive travel itinerary.

## Usage

```bash
/travel_planner destination=Tokyo start_date="2026-04-01" end_date="2026-04-05"
```

## Parameters

- **destination**: Travel destination city or country
- **start_date**: Trip start date
- **end_date**: Trip end date
- **budget**: Budget range (low, medium, high, default: medium)
- **interests**: Comma-separated interests (culture, nature, food, adventure, default: culture,food)

## Examples

```
travel_planner destination=Tokyo start_date="2026-04-01" end_date="2026-04-05" budget=high interests="culture,food,adventure"
```

## Error Handling

- **Outdated info:** Note that prices and availability should be verified.
- **Travel restrictions:** Search for current entry requirements.
