---
name: language_practice
description: Practice a foreign language with vocabulary drills, sentence construction, and conversation exercises
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🗣️"
parameters:
  language:
    type: string
    description: "Language to practice"
  level:
    type: string
    description: "Proficiency level: beginner, intermediate, advanced"
    default: "beginner"
  exercise_type:
    type: string
    description: "Exercise type: vocabulary, sentences, conversation, or grammar"
    default: "vocabulary"
  score:
    type: number
    description: "Score from practice session"
required: [language]
steps:
  - id: search_pronunciation
    tool: google_search
    args:
      query: "how to pronounce '{{word}}' in {{language}}"
      num: 3
    optional: true
  - id: log_progress
    tool: ShellTool
    args:
      command: "echo '$(date +%Y-%m-%d),{{language}},{{score}}' >> ~/stackowl_language_log.csv"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: start_practice
    type: llm
    prompt: "Conduct a {{exercise_type}} practice session in {{language}} for a {{level}} level learner. For vocabulary: present words and ask for translations. For sentences: give words and ask user to construct sentences. For conversation: engage in dialogue. For grammar: present sentences with errors to fix."
    depends_on: [search_pronunciation]
    inputs: [search_pronunciation.output]
---

# Language Practice

Practice a foreign language.

## Usage

```bash
/language_practice language=<lang> level=<level> exercise_type=<type> score=<score>
```

## Parameters

- **language**: Language to practice
- **level**: Proficiency level: beginner, intermediate, advanced (default: beginner)
- **exercise_type**: Exercise type: vocabulary, sentences, conversation, or grammar (default: vocabulary)
- **score**: Score from practice session

## Examples

### Spanish vocabulary drill

```
language=Spanish
level=beginner
exercise_type=vocabulary
```

## Error Handling

- **Unknown language:** Ask user to specify from supported languages.
- **No previous sessions:** Start with beginner-level basics.
