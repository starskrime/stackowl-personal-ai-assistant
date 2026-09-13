<!-- bmad:context -->
<!-- Verified 2026-09-12 against 0ce773ccb47c79cba4999a4b0aaa8ccde40421b3. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## stackowl

Self-hosted personal AI assistant platform. Python managed with `uv` (version in `pyproject.toml`); durable state is SQLite and markdown under the StackOwl home, never in the repository. Designs and references live in `docs/`.

<!-- /bmad:context -->

<!-- Maintained by hand OUTSIDE the managed block, so a refresh cannot drop it. tests/cli/test_db_query.py and tests/db/test_time_is_stored_one_way.py pin these lines. -->

## Runtime state

- Read the live database with `uv run stackowl db query "SELECT ..."` — it resolves the path through `StackowlHome`, opens read-only, prints the path it used on stderr, and refuses a missing or empty file. The `sqlite3` CLI is not installed on the dev box.
- The live database is `StackowlHome.db_path()`, `~/.stackowl/workspace/stackowl.db` by default. Never open `~/.stackowl/stackowl.db`: SQLite creates a missing file, and that empty guess reads as total data loss.
- Open a SQLite file you only read with a `file:<path>?mode=ro` URI (`stackowl.db.readonly.connect_read_only` in code), never a bare path. That never creates a database file, though SQLite may still add `-wal`/`-shm` beside a WAL database.
- Build every path under the home from `stackowl.paths.StackowlHome`, never `Path.home() / ".stackowl"`; `STACKOWL_HOME` and `STACKOWL_DATA_DIR` move it, and the test suite isolates it.
- Change data through migrations (`uv run stackowl db migrate`), never an ad-hoc write.

## Conventions

- One clock, one format: store every moment as ISO-8601 TEXT in UTC; the 28 older epoch columns are named in `tests/db/test_time_is_stored_one_way.py`.
- Never compare a stored timestamp to SQLite's `datetime('now', ...)` — it renders `2026-09-05 15:07:19` against a stored `2026-09-05T00:00:50+00:00`, and `T` sorts after a space, so every row on the bound's date passes; bind a Python-computed ISO string instead.
