---
name: database_query
description: Execute SQL queries against SQLite, PostgreSQL, or MySQL databases and format the results
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🗄️"
parameters:
  db_path:
    type: string
    description: "Path to SQLite database (for SQLite queries)"
    default: ""
  query:
    type: string
    description: "SQL query to execute"
  db_type:
    type: string
    description: "Database type (sqlite, postgres, mysql)"
    default: "sqlite"
  host:
    type: string
    description: "Database host (for postgres/mysql)"
    default: "localhost"
  user:
    type: string
    description: "Database user (for postgres/mysql)"
    default: ""
  database:
    type: string
    description: "Database name (for postgres/mysql)"
    default: ""
required: [query]
steps:
  - id: execute_sqlite
    tool: ShellTool
    args:
      command: "sqlite3 -header -column '{{db_path}}' '{{query}}' 2>&1"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: execute_postgres
    tool: ShellTool
    args:
      command: "PGPASSWORD='{{password}}' psql -h '{{host}}' -U '{{user}}' -d '{{database}}' -c '{{query}}' 2>&1 || echo 'PostgreSQL query failed'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: execute_mysql
    tool: ShellTool
    args:
      command: "mysql -h '{{host}}' -u '{{user}}' -p'{{password}}' '{{database}}' -e '{{query}}' 2>&1 || echo 'MySQL query failed'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: present_results
    type: llm
    prompt: "Format the SQL query results as a clean table. If there's an error, explain it clearly.\n\nQuery: {{query}}\nResults:\n{{#if execute_sqlite.output}}{{execute_sqlite.output}}{{/if}}{{#if execute_postgres.output}}{{execute_postgres.output}}{{/if}}{{#if execute_mysql.output}}{{execute_mysql.output}}{{/if}}"
    depends_on: [execute_sqlite, execute_postgres, execute_mysql]
    inputs: [execute_sqlite.output, execute_postgres.output, execute_mysql.output]
---

# Database Query

Execute SQL queries and display formatted results.

## Steps

1. **Identify the database type:**
   - SQLite: `sqlite3 <db_path> '<query>'`
   - PostgreSQL: `psql -h <host> -U <user> -d <db> -c '<query>'`
   - MySQL: `mysql -h <host> -u <user> -p<pass> -e '<query>' <db>`
2. **Execute the query:**
   ```bash
   sqlite3 -header -column <db_path> '<SQL_query>'
   ```
3. **Format and present results** as a table.

## Examples

### Query SQLite

```
db_path="./data.db"
query="SELECT * FROM users LIMIT 10"
db_type="sqlite"
```

## Error Handling

- **Database not found:** Check file path or connection string.
- **Syntax error:** Show the error and suggest corrections.
- **Destructive query (DROP/DELETE):** Warn user and require explicit confirmation.
