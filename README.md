# StackOwl

A self-hosted personal AI assistant that runs as a **persistent process**, not a chat
window. You give it a goal once; it schedules the work, retries what fails, and delivers
the answer to wherever you actually are — terminal, Telegram, Slack.

Everything runs on your own machine against your own model provider. No hosted service.

---

## Requirements

* **Python 3.13+** (the version `pyproject.toml` requires)
* [`uv`](https://github.com/astral-sh/uv) for dependency management
* A model provider it can reach — any OpenAI-compatible endpoint, including a local one

It is developed on an NVIDIA Jetson, so it is expected to run on modest hardware.

## Install

```bash
git clone <this repository>
cd stackowl-personal-ai-assistant
uv sync
```

Then create the installation — this makes `~/.stackowl/` and applies every database
migration:

```bash
uv run stackowl init
```

## Set it up

Pick one:

```bash
uv run stackowl setup --minimal            # 3 steps: provider, API key, test call
uv run stackowl setup --demo               # non-interactive, no API key required
uv run stackowl setup --channel telegram   # add a delivery channel
```

Then confirm the configuration actually resolves — this loads `stackowl.yaml` and
resolves every provider secret, so it fails loudly now rather than mid-turn later:

```bash
uv run stackowl validate-config
```

## Run it

```bash
uv run stackowl start
```

To keep it running across reboots:

```bash
uv run stackowl install-service
```

`stackowl stop` sends SIGTERM to the PID file. During development, `./start.sh` restarts
a local instance and is what this repository's own workflow uses.

## Where your data lives

Everything is under **`~/.stackowl/`** — override with the `STACKOWL_HOME` environment
variable, which is also how you run two instances side by side.

| Path | What it is |
|---|---|
| `~/.stackowl/stackowl.yaml` | your configuration |
| `~/.stackowl/stackowl.db` | SQLite: tasks, memory, outcomes, audit trail |
| `~/.stackowl/logs/` | newline-delimited JSON logs |

Nothing is written into the project directory. `stackowl backup` takes an atomic backup
of every store; `stackowl export` and `stackowl import` move an installation between
machines.

## Finding your way around

```bash
uv run stackowl --help          # every command
uv run stackowl health          # system health
uv run stackowl trace           # per-stage latency for a request
uv run stackowl providers       # manage AI providers
uv run stackowl db              # database management
```

## Developing

```bash
./scripts/tripwires.sh          # ~2 min: cross-cutting guards, lint and type baselines
./scripts/full_suite.sh         # the full suite, detached — it takes ~33 minutes
```

The full suite is the only thing that detects cross-test pollution, so run it before
anything structural. It writes a stamped log and states which tree the verdict is about.

`docs/reference-mapping/PROCESS.md` is how work is done here, and
`docs/reference-mapping/DOC_STANDARD.md` is what every design document must contain.
`progress.yml` is the state of record.

## Licence

MIT.
