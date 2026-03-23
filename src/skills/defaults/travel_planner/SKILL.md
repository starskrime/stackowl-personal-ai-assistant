---
name: travel_planner
description: Create a detailed travel itinerary with flights, hotels, attractions, and daily schedules
openclaw:
  emoji: "✈️"
---
# Travel Planner
Create a comprehensive travel itinerary.
## Steps
1. **Collect trip details:**
   - Destination
   - Travel dates
   - Budget range
   - Interests (culture, nature, food, adventure)
2. **Research the destination:**
   ```
   web_search query="<destination> travel guide 2026"
   web_search query="<destination> top attractions"
   web_search query="<destination> best restaurants"
   web_search query="<destination> weather <month>"
   ```
3. **Build daily itinerary:**
   ```markdown
   ## Day 1: Arrival
   - Morning: Check-in at hotel
   - Afternoon: Visit <attraction>
   - Evening: Dinner at <restaurant>
   ```
4. **Include practical info:** currency, local transport, tips.
## Examples
### Plan Tokyo trip
```
web_search query="Tokyo 5 day itinerary 2026"
web_search query="Tokyo best restaurants budget"
```
## Error Handling
- **Outdated info:** Note that prices and availability should be verified.
- **Travel restrictions:** Search for current entry requirements.
