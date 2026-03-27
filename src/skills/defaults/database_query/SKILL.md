---
name: database_query
description: Execute SQL queries against SQLite, PostgreSQL, or MySQL databases and format the results
openclaw:
  emoji: "🗄️"
---

# Database Query

Execute SQL queries and display formatted results.

## Steps

1. **Identify the database type:**
   - SQLite: `run_shell_command("sqlite3 <db_path> '<query>'")`
   - PostgreSQL: `run_shell_command("psql -h <host> -U <user> -d <db> -c '<query>'")`
   - MySQL: `run_shell_command("mysql -h <host> -u <user> -p<pass> -e '<query>' <db>")`
2. **Execute the query:**
   ```bash
   run_shell_command("sqlite3 -header -column <db_path> '<SQL_query>'")
   ```
3. **Format and present results** as a table.

## Examples

### Query SQLite

```bash
run_shell_command("sqlite3 -header -column data.db 'SELECT * FROM users LIMIT 10'")
```

## Error Handling

- **Database not found:** Check file path or connection string.
- **Syntax error:** Show the error and suggest corrections.
- **Destructive query (DROP/DELETE):** Warn user and require explicit confirmation.
