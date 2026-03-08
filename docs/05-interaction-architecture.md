# StackOwl — Interaction Architecture

## The Noctua Model

StackOwl uses a "Boss → Secretary → Team" interaction model:

```
┌──────────┐     always     ┌───────────┐     delegates     ┌──────────────┐
│          │ ◄────────────► │           │ ───────────────►  │  Specialist  │
│   USER   │                │  NOCTUA   │                   │    Owls      │
│  (Boss)  │  proactive     │  (Exec    │  ◄───────────── │  (Team)      │
│          │  pings          │  Asst)    │     insights      │              │
└──────────┘                └───────────┘                   └──────────────┘
                                                             • Archimedes
                                                             • Athena
                                                             • Scrooge
                                                             • Socrates
                                                             • Mercury
                                                             • Newton
```

### How It Works
1. **User always talks to Noctua** — she's your personal executive assistant
2. **Noctua delegates internally** — when you ask a deep technical question, she consults specialist owls behind the scenes
3. **Noctua is proactive** — she doesn't just wait for questions:
   - 🌅 Morning brief at 9am
   - 💡 Ideas and follow-ups based on recent conversations
   - 🍽️ Lunch reminders
   - 📊 End-of-day summary offers
   - ⚠️ Alerts from Perch Points
4. **Quiet hours respected** — no pings between 10pm-7am

### Proactive Behaviors
| Type | When | What |
|---|---|---|
| Morning Brief | 9:00 AM daily | Day overview, pending items, motivation |
| Check-in | Every 2 hours | Contextual follow-up or offer to help |
| Lunch Reminder | 12-1 PM | Casual lunch nudge |
| EOD Summary | 5-6 PM | Offer to summarize the day |
| Follow-up | After idle periods | Reference recent conversation topics |
| Alert | On Perch Point trigger | File changes, git events, log anomalies |

### Available via
- **Telegram** — always-on, proactive pings delivered as messages
- **CLI** — interactive terminal sessions
- **Both** — simultaneously
