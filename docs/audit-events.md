# Audit event vocabulary

StackOwl writes every consequential action to a SHA-256-chained audit log in SQLite (`audit_log` table). Each row carries `event_type`, `actor`, `target` (optional URL path), and a JSON `details` blob.

## Browser events

Emitted by the Camoufox-backed browser tools. Read-only actions (`web_fetch`, `browser_extract`, `browser_screenshot`, `browser_recall_url`) are **not** audited — they would flood the chain.

### `browser_eval_js`
```
actor:   <owl_name or "browser_tool">
target:  <url-path-only>
details:
  session_id:           <uuid-hex>
  script_len:           <int>
  script_sha256_prefix: <first 16 hex chars>
```
Script *contents* are never persisted. The hash prefix lets you correlate an audit row with the actual script if the LLM still has it in conversation.

### `browser_upload`
```
actor:   <owl_name>
target:  <url-path-only>
details:
  session_id:   <uuid-hex>
  selector_len: <int>
  file_path:    <absolute path of uploaded file>
```

### `browser_download`
```
actor:   <owl_name>
target:  <url-path-only>
details:
  session_id:  <uuid-hex>
  bytes:       <int>
  sha256:      <hex digest of file contents>
  stored_path: <path under ~/.stackowl/downloads/>
```

### `browser_cookies_set` / `browser_cookies_clear`
```
actor:   <owl_name>
target:  null
details:
  session_id:   <uuid-hex>
  cookie_count: <int>     # set only
```

### `browser_browse`
**One row per meta-tool invocation**, aggregated via `BatchAuditLogger`:
```
actor:   <owner_key>
target:  <seed_url-path-only or null>
details:
  task_len:        <int>
  allowed_domains: [<host>, ...]
  step_count:      <int>
  steps:           [{step, action, url}, ...]
  exception:       null | "TypeName: message"
```
Each step's `action` dict mirrors what the inner LLM emitted (e.g., `{action: "click_index", index: 4}`).

### MCP-bridged calls
When an external MCP client invokes a browser tool, the actor is `mcp:<client_id>` and event type is the tool's normal name. The exposure policy denies the consequential set by default — set `mcp_server.allow_browser_writes: true` in `stackowl.yaml` to permit them, and they will then appear in the audit chain.

## Scheduler events (browser-related)

### `browser_session_recycled`
Emitted by `BrowserSessionRecycleHandler` on every hourly tick:
```
actor:   "scheduler"
target:  null
details:
  evicted_sessions:   <int>
  recycle_check_ok:   <bool>
```

### `website_changed`
Published to the EventBus (not the audit chain — too high-volume).

## Tail / export

```bash
# Last 50 audit rows
sqlite3 ~/.stackowl/workspace/stackowl.db \
  "SELECT timestamp, event_type, actor, target FROM audit_log ORDER BY audit_id DESC LIMIT 50"

# Signed export for sharing
stackowl audit export --output audit-export.json
```

Signed exports include the chain's last `integrity_hash`; verifying it lets a reviewer detect tampering since the export was generated.
