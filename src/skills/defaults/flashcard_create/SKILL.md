---
name: flashcard_create
description: Create study flashcards from notes, documents, or topics and save them for spaced repetition review
openclaw:
  emoji: "🃏"
---
# Flashcard Creator
Create study flashcards.
## Steps
1. **Get source material** from user (notes, topic, or file):
   ```bash
   read_file("<notes_file>")
   ```
2. **Generate Q&A pairs** from the material.
3. **Save flashcards** as a structured file:
   ```bash
   write_file("~/flashcards/<topic>.md", "# <Topic> Flashcards\n\n**Q:** <question>\n**A:** <answer>\n\n---\n")
   ```
4. **Present** a few sample cards for review.
## Examples
### Create from a topic
```bash
write_file("~/flashcards/javascript_basics.md", "# JavaScript Basics\n\n**Q:** What is a closure?\n**A:** A function that retains access to its lexical scope...\n")
```
## Error Handling
- **Source too long:** Extract most important concepts only.
- **Directory doesn't exist:** Create with `mkdir -p ~/flashcards`.
