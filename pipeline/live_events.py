#!/usr/bin/env python3
r"""The orchestrator's decisions, written down as they are made.

A run directory shows what OpenROAD is doing; it cannot show why this
candidate exists, which repair produced it, or whether polish adopted a
move. Those decisions live in orchestrate()'s memory and, until now, in
its stdout - fine in a terminal you happen to be watching, invisible to
anything that attaches later.

This appends one JSON object per line to <design>/.events.jsonl,
which live_view.py reads beside the run directories.

It sits in the design directory, not under runs/. That was the first
choice, and it silently wrote nothing on the WSL runner: the container
creates runs/ as root, so the host user cannot create a file in it (the
same reason claim_run_dir() exists). The design directory is the host
user's own.

Writing an event must never cost a run: every failure here is swallowed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

EVENTS_NAME = ".events.jsonl"  # gitignored: pipeline/designs/*/.events.jsonl


def events_path(design_dir: Path | str) -> Path:
    return Path(design_dir) / EVENTS_NAME


def emit(design_dir: Path | str, type: str, **fields) -> None:
    """Append one event. A single write() of one line, so concurrent
    candidate threads cannot interleave inside a record."""
    try:
        path = events_path(design_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"t": round(time.time(), 3), "type": type, **fields},
                          default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - observation must not break a run
        pass


def read(design_dir: Path | str) -> list[dict]:
    """The events of the latest orchestrate() run: everything after the
    last `run_start`. Unparseable lines (a write caught half-done) are
    skipped rather than raised."""
    out: list[dict] = []
    # Scan backwards in bounded blocks, stopping at the latest run_start.
    # Old runs can be arbitrarily large; a one-second observer refresh
    # should only read and parse the run it is displaying. Keep bytes until
    # a whole line is assembled so block boundaries cannot split UTF-8.
    try:
        with open(events_path(design_dir), "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            pending = b""
            while pos:
                size = min(pos, 64 * 1024)
                pos -= size
                f.seek(pos)
                lines = (f.read(size) + pending).split(b"\n")
                pending = lines.pop(0) if pos else b""
                for line in reversed(lines):
                    try:
                        ev = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(ev, dict):
                        out.append(ev)
                        if ev.get("type") == "run_start":
                            return list(reversed(out))
    except OSError:
        return []
    return list(reversed(out))
