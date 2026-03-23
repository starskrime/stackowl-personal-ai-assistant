---
name: language_practice
description: Practice a foreign language with vocabulary drills, sentence construction, and conversation exercises
openclaw:
  emoji: "🗣️"
---
# Language Practice
Practice a foreign language.
## Steps
1. **Identify the language** and user's level (beginner, intermediate, advanced).
2. **Choose exercise type:**
   - **Vocabulary drill:** Present word → ask for translation
   - **Sentence building:** Give words → user constructs sentence
   - **Conversation:** Dialogue practice in target language
   - **Grammar quiz:** Present sentence with error → ask user to fix
3. **Validate answers** and provide corrections.
4. **Web search for pronunciation** if needed:
   ```
   web_search query="how to pronounce '<word>' in <language>"
   ```
5. **Track progress** in a practice log:
   ```bash
   run_shell_command("echo '$(date +%Y-%m-%d),<language>,<score>' >> ~/stackowl_language_log.csv")
   ```
## Examples
### Spanish vocabulary drill
```
Word: "house"
Your answer: ___
Correct: "casa" ✅
```
## Error Handling
- **Unknown language:** Ask user to specify from supported languages.
- **No previous sessions:** Start with beginner-level basics.
