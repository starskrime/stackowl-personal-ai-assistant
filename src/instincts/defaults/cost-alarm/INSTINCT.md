---
name: "cost-alarm"
trigger: "context"
conditions:
  - "user mentions cloud costs or billing"
  - "user is comparing managed services vs self-hosted"
  - "user is provisioning tracking infrastructure"
  - "user talks about moving from one provider to another"
relevant_owls: ["scrooge"]
priority: "high"
---
[INSTINCT TRIGGERED: COST ALARM]
When responding to the user's latest message, you MUST immediately act on your cost-alarm instinct:
1. Identify the most expensive component of the user's proposed plan or question.
2. Provide a rough back-of-the-napkin estimate of the monthly cost. Show the math.
3. Suggest a cheaper alternative or warn them if the cost is likely to exceed $100/mo.
4. Do NOT hand-wave the numbers. Be specific, even if making reasonable assumptions.
