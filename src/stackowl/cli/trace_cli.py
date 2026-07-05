"""trace CLI — reconstruct one request's latency waterfall from the JSONL log.

Reads ``~/.stackowl/logs/stackowl*.jsonl``, filters by ``trace_id``, and
renders every log line as a waterfall ordered by timestamp: offset from the
turn's first line, this line's own ``duration_ms`` (when the call site
reported one), and depth in the span tree (via ``parent_span_id``). This is
the map from telegram receive through the pipeline, provider calls, tool
dispatch, and back out to delivery — the latency instrumentation is spread
across the call sites; this command is just the reader.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

trace_app = typer.Typer(help="Inspect request traces and per-stage latency.")


def _log_files() -> list[Path]:
    from stackowl.paths import StackowlHome

    log_dir = StackowlHome.logs_dir()
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("stackowl*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def _load_entries(trace_id: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _log_files():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or f'"{trace_id}"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("trace_id") == trace_id:
                        entries.append(record)
        except OSError:
            continue
    entries.sort(key=lambda r: r.get("ts", ""))
    return entries


def _depth_map(entries: list[dict[str, Any]]) -> dict[str, int]:
    """span_id -> depth, walking parent_span_id chains (cycle-safe)."""
    parent_of: dict[str, str | None] = {}
    for e in entries:
        span = e.get("span_id")
        if span and span not in parent_of:
            parent_of[span] = e.get("parent_span_id")

    depth: dict[str, int] = {}

    def _depth(span: str, seen: frozenset[str]) -> int:
        if span in depth:
            return depth[span]
        parent = parent_of.get(span)
        if not parent or parent == span or parent in seen:
            depth[span] = 0
        else:
            depth[span] = 1 + _depth(parent, seen | {span})
        return depth[span]

    for span in parent_of:
        _depth(span, frozenset())
    return depth


@trace_app.command("show")
def show(
    trace_id: str = typer.Argument(
        ..., help="The trace_id to reconstruct (copy it from any log line for the request)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw filtered entries as JSON instead of a waterfall."
    ),
) -> None:
    """Print the latency waterfall for one request: telegram receive -> ... -> delivery.

    Depth in the printed tree comes from parent_span_id — most call sites
    still share one span_id per turn unless they explicitly open a child
    span (see traced_span / TraceContext.span usage across the
    pipeline/provider/tool layers). duration_ms is whatever the call site
    measured for that line; lines with no duration are entry/decision
    markers, not timed spans.
    """
    entries = _load_entries(trace_id)
    if not entries:
        typer.echo(f"No log lines found for trace_id={trace_id}", err=True)
        raise typer.Exit(1)

    if json_out:
        typer.echo(json.dumps(entries, indent=2, default=str))
        return

    depth = _depth_map(entries)
    t0 = datetime.fromisoformat(entries[0]["ts"])
    typer.echo(f"trace_id={trace_id}  ({len(entries)} lines)")
    typer.echo(f"{'offset_ms':>10}  {'duration_ms':>11}  {'lvl':<5} {'module':<22} msg")
    for e in entries:
        ts = datetime.fromisoformat(e["ts"])
        offset_ms = (ts - t0).total_seconds() * 1000
        dur = e.get("duration_ms")
        span = e.get("span_id") or ""
        indent = "  " * depth.get(span, 0)
        dur_str = f"{dur:.0f}" if isinstance(dur, (int, float)) else ""
        typer.echo(
            f"{offset_ms:>10.0f}  {dur_str:>11}  {e.get('level', ''):<5} "
            f"{e.get('module', ''):<22} {indent}{e.get('msg', '')}"
        )

    total_ms = (datetime.fromisoformat(entries[-1]["ts"]) - t0).total_seconds() * 1000
    typer.echo(f"\nSpan from first to last log line: {total_ms:.0f}ms")
